#!/usr/bin/env bash
# KOSPI200 선물 월물별 1분봉 백필 공통 함수.

set -Eeuo pipefail

FUTURES_BACKFILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FUTURES_BACKFILL_CONTAINER="${TIMESCALEDB_CONTAINER:-trading-system-v2-timescaledb-test}"
FUTURES_BACKFILL_VENV="${FUTURES_BACKFILL_VENV:-${FUTURES_BACKFILL_ROOT}/venv}"
FUTURES_BACKFILL_ENV="${FUTURES_BACKFILL_ENV:-${FUTURES_BACKFILL_ROOT}/.env}"
FUTURES_BACKFILL_RUNNER="${FUTURES_BACKFILL_ROOT}/scripts/backfill/run_futures_minute_backfill.py"
FUTURES_BACKFILL_START_DATE="${FUTURES_BACKFILL_START_DATE:-2026-05-27}"
FUTURES_BACKFILL_END_DATE="${FUTURES_BACKFILL_END_DATE:-2026-07-28}"
FUTURES_BACKFILL_INTERVAL="${FUTURES_BACKFILL_INTERVAL:-1.0}"
FUTURES_BACKFILL_MONITOR_INTERVAL="${FUTURES_BACKFILL_MONITOR_INTERVAL:-10}"

futures_die() { echo "오류: $*" >&2; exit 1; }
futures_log() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"; }

futures_assert_job_id() {
    [[ "$1" =~ ^[0-9]+$ ]] || { echo "오류: job_id는 숫자만 사용할 수 있습니다." >&2; return 1; }
}

futures_validate_env_file() {
    [[ -f "$FUTURES_BACKFILL_ENV" ]] || futures_die ".env 파일이 없습니다."
    local line trimmed
    while IFS= read -r line || [[ -n "$line" ]]; do
        trimmed="${line#"${line%%[![:space:]]*}"}"
        [[ -z "$trimmed" || "$trimmed" == \#* ]] && continue
        [[ "$trimmed" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || futures_die ".env 형식이 올바르지 않습니다."
        [[ "${trimmed#*=}" != *'$('* && "${trimmed#*=}" != *'`'* && "${trimmed#*=}" != *';'* && "${trimmed#*=}" != *'|'* ]] || futures_die ".env에 실행 가능한 셸 문자가 포함되어 있습니다."
    done < "$FUTURES_BACKFILL_ENV"
}

futures_prepare_database() {
    futures_validate_env_file
    cd "$FUTURES_BACKFILL_ROOT"
    set -a
    # shellcheck disable=SC1090
    source "$FUTURES_BACKFILL_ENV"
    set +a
    [[ -f "${FUTURES_BACKFILL_VENV}/bin/activate" ]] || futures_die "가상환경이 없습니다: ${FUTURES_BACKFILL_VENV}"
    # shellcheck disable=SC1090
    source "${FUTURES_BACKFILL_VENV}/bin/activate"
    [[ "${DB_NAME:-}" == "trading_system_v2_test" ]] || futures_die "운영 DB 보호: DB_NAME은 trading_system_v2_test여야 합니다."
    export DB_INTEGRATION_TEST=1
    local name
    for name in DB_PASSWORD; do
        [[ -n "${!name:-}" ]] || futures_die "필수 환경변수가 없습니다: ${name}"
    done
    command -v docker >/dev/null 2>&1 || futures_die "docker 명령을 찾을 수 없습니다."
    local health
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$FUTURES_BACKFILL_CONTAINER" 2>/dev/null || true)"
    [[ "$health" == "healthy" ]] || futures_die "TimescaleDB 컨테이너가 healthy 상태가 아닙니다."
}

futures_bootstrap() {
    futures_prepare_database
    local name
    for name in KIS_BASE_URL KIS_API_KEY KIS_API_SECRET; do
        [[ -n "${!name:-}" ]] || futures_die "필수 환경변수가 없습니다: ${name}"
    done
}

futures_bootstrap_database_only() {
    futures_prepare_database
}

futures_psql() {
    docker exec "$FUTURES_BACKFILL_CONTAINER" psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" "$@"
}

futures_wait_for_job() {
    local baseline="$1" candidate
    for _ in {1..90}; do
        candidate="$(futures_psql -At -c "SELECT job_id FROM backfill_job WHERE job_id > ${baseline} AND job_type = 'FUTURES_MINUTE_KRX' ORDER BY job_id DESC LIMIT 1;" 2>/dev/null || true)"
        if [[ "$candidate" =~ ^[0-9]+$ ]]; then printf '%s\n' "$candidate"; return 0; fi
        sleep 1
    done
    return 1
}

futures_print_status() {
    local job_id="$1"
    futures_assert_job_id "$job_id" || return 1
    futures_psql -P pager=off -c "
        SELECT job_id, status, start_date, end_date, started_at, completed_at, failure_message
        FROM backfill_job WHERE job_id = ${job_id};
        SELECT status, count(*) AS segment_count, sum(request_count) AS api_calls,
               sum(returned_count) AS fetched, sum(inserted_count) AS inserted,
               sum(duplicate_count) AS duplicates
        FROM backfill_segment WHERE job_id = ${job_id}
        GROUP BY status ORDER BY status;
    "
}

futures_verify_job() {
    local job_id="$1"
    futures_assert_job_id "$job_id" || return 1
    futures_psql -P pager=off -c "
        SELECT instrument_code, status, count(*) AS segment_count,
               sum(request_count) AS api_calls, sum(returned_count) AS fetched,
               sum(inserted_count) AS inserted, sum(duplicate_count) AS duplicates,
               min(minimum_bar_time) AS first_bar_time, max(maximum_bar_time) AS last_bar_time
        FROM backfill_segment WHERE job_id = ${job_id}
        GROUP BY instrument_code, status ORDER BY instrument_code, status;

        SELECT segment.instrument_code, segment.trade_date
        FROM backfill_segment AS segment
        LEFT JOIN raw_futures_minute AS raw
          ON raw.futures_code = segment.instrument_code
         AND raw.bar_time::date = segment.trade_date
         AND raw.trading_venue = 'KRX' AND raw.collect_cycle = '1MIN'
        WHERE segment.job_id = ${job_id} AND segment.status = 'COMPLETED'
        GROUP BY segment.segment_id, segment.instrument_code, segment.trade_date
        HAVING count(raw.bar_time) = 0
        ORDER BY segment.instrument_code, segment.trade_date;

        SELECT raw.futures_code, count(*) AS stored_count,
               count(DISTINCT (raw.bar_time, raw.data_source, raw.market_code, raw.trading_venue, raw.collect_cycle, raw.futures_code)) AS distinct_primary_key_count,
               count(*) FILTER (WHERE raw.trading_venue = 'KRX') AS krx_count,
               count(*) FILTER (WHERE raw.collect_cycle = '1MIN') AS one_minute_count,
               count(*) FILTER (WHERE raw.raw_payload ? 'stck_bsop_date' AND raw.raw_payload ? 'stck_cntg_hour' AND raw.raw_payload ? 'futs_prpr') AS payload_preserved_count
        FROM raw_futures_minute AS raw
        JOIN backfill_segment AS segment
          ON segment.instrument_code = raw.futures_code AND segment.trade_date = raw.bar_time::date
        WHERE segment.job_id = ${job_id}
        GROUP BY raw.futures_code ORDER BY raw.futures_code;
    "
    local result
    result="$(futures_psql -At -c "
        WITH segments AS (
            SELECT * FROM backfill_segment WHERE job_id = ${job_id}
        ), missing_raw AS (
            SELECT 1
            FROM segments AS segment
            LEFT JOIN raw_futures_minute AS raw
              ON raw.futures_code = segment.instrument_code
             AND raw.bar_time::date = segment.trade_date
             AND raw.trading_venue = 'KRX' AND raw.collect_cycle = '1MIN'
            WHERE segment.status = 'COMPLETED'
            GROUP BY segment.segment_id
            HAVING count(raw.bar_time) = 0
        ), invalid_raw AS (
            SELECT 1
            FROM raw_futures_minute AS raw
            JOIN segments AS segment
              ON segment.instrument_code = raw.futures_code
             AND segment.trade_date = raw.bar_time::date
            WHERE raw.trading_venue <> 'KRX' OR raw.collect_cycle <> '1MIN'
               OR NOT (raw.raw_payload ? 'stck_bsop_date'
                       AND raw.raw_payload ? 'stck_cntg_hour'
                       AND raw.raw_payload ? 'futs_prpr')
        )
        SELECT CASE
            WHEN EXISTS (SELECT 1 FROM segments WHERE status IN ('PENDING', 'RUNNING', 'FAILED')) THEN 'INCOMPLETE'
            WHEN EXISTS (SELECT 1 FROM missing_raw) THEN 'MISSING_RAW'
            WHEN EXISTS (SELECT 1 FROM invalid_raw) THEN 'INVALID_RAW'
            ELSE 'PASS'
        END;
    ")" || return 1
    [[ "$result" == "PASS" ]] || { echo "오류: 선물 백필 검증 판정=${result}" >&2; return 1; }
    futures_log "선물 백필 검증 판정: PASS"
}
