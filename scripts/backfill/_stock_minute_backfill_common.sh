#!/usr/bin/env bash
# KRX 주식·ETF 1분봉 백필 실행 스크립트 공통 함수.

set -Eeuo pipefail

BACKFILL_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKFILL_CONTAINER="${TIMESCALEDB_CONTAINER:-trading-system-v2-timescaledb-test}"
BACKFILL_START_DATE="${BACKFILL_START_DATE:-2026-05-27}"
BACKFILL_END_DATE="${BACKFILL_END_DATE:-2026-07-28}"
BACKFILL_REQUEST_INTERVAL="${BACKFILL_REQUEST_INTERVAL:-1.0}"
BACKFILL_MONITOR_INTERVAL="${BACKFILL_MONITOR_INTERVAL:-10}"
BACKFILL_VENV_PATH="${BACKFILL_VENV_PATH:-${BACKFILL_PROJECT_ROOT}/venv}"
BACKFILL_ENV_FILE="${BACKFILL_ENV_FILE:-${BACKFILL_PROJECT_ROOT}/.env}"
BACKFILL_RUNNER="${BACKFILL_PROJECT_ROOT}/scripts/backfill/run_stock_minute_backfill.py"
BACKFILL_SEED_FILE="${BACKFILL_PROJECT_ROOT}/database/seed/01_stock_minute_backfill_targets.sql"
BACKFILL_LOG_DIR="${BACKFILL_LOG_DIR:-${BACKFILL_PROJECT_ROOT}/logs/backfill}"

BACKFILL_WORKER_PID=""
BACKFILL_JOB_ID=""
BACKFILL_LOG_FILE=""
BACKFILL_WORKER_LOG_FILE=""

backfill_die() {
    echo "오류: $*" >&2
    exit 1
}

backfill_log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*"
}

backfill_setup_log() {
    local mode="$1"
    mkdir -p "$BACKFILL_LOG_DIR"
    local stamp
    stamp="$(date '+%Y%m%d-%H%M%S')"
    BACKFILL_LOG_FILE="${BACKFILL_LOG_DIR}/${mode}-${stamp}.log"
    BACKFILL_WORKER_LOG_FILE="${BACKFILL_LOG_DIR}/${mode}-${stamp}.worker.log"
    exec > >(tee -a "$BACKFILL_LOG_FILE") 2>&1
    backfill_log "실행 로그: ${BACKFILL_LOG_FILE}"
    backfill_log "백필 작업 로그: ${BACKFILL_WORKER_LOG_FILE}"
}

backfill_validate_dotenv() {
    local dotenv_path="$BACKFILL_ENV_FILE"
    [[ -f "$dotenv_path" ]] || backfill_die ".env 파일이 없습니다."

    local line line_number=0 trimmed value
    while IFS= read -r line || [[ -n "$line" ]]; do
        ((line_number += 1))
        trimmed="${line#"${line%%[![:space:]]*}"}"
        [[ -z "$trimmed" || "$trimmed" == \#* ]] && continue
        [[ "$trimmed" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || backfill_die ".env ${line_number}행은 환경변수 할당문이 아닙니다."
        value="${trimmed#*=}"
        [[ "$value" != *'$('* && "$value" != *'`'* && "$value" != *';'* && "$value" != *'|'* ]] \
            || backfill_die ".env ${line_number}행에 실행 가능한 셸 문자가 포함되어 있습니다."
    done < "$dotenv_path"
}

backfill_load_env() {
    backfill_validate_dotenv
    set -a
    # shellcheck disable=SC1090
    source "$BACKFILL_ENV_FILE"
    set +a
}

backfill_activate_venv() {
    [[ -f "${BACKFILL_VENV_PATH}/bin/activate" ]] \
        || backfill_die "가상환경 활성화 파일이 없습니다: ${BACKFILL_VENV_PATH}/bin/activate"
    # shellcheck disable=SC1090
    source "${BACKFILL_VENV_PATH}/bin/activate"
    BACKFILL_PYTHON_BIN="${PYTHON_BIN:-python}"
    command -v "$BACKFILL_PYTHON_BIN" >/dev/null 2>&1 \
        || backfill_die "Python 실행 파일을 찾을 수 없습니다."
}

backfill_require_database_environment() {
    local required=(DB_PASSWORD)
    local name
    for name in "${required[@]}"; do
        [[ -n "${!name:-}" ]] || backfill_die "필수 환경변수가 없습니다: ${name}"
    done

    export DB_INTEGRATION_TEST=1
    export DB_HOST="${DB_HOST:-127.0.0.1}"
    export DB_PORT="${DB_PORT:-5432}"
    export DB_NAME="${DB_NAME:-trading_system_v2_test}"
    export DB_USER="${DB_USER:-trading_test}"

    [[ "$DB_NAME" == "trading_system_v2_test" ]] \
        || backfill_die "운영 DB 보호: DB_NAME은 trading_system_v2_test여야 합니다. 현재 값은 사용하지 않습니다."
}

backfill_require_environment() {
    backfill_require_database_environment

    local required=(KIS_BASE_URL KIS_API_KEY KIS_API_SECRET)
    local name
    for name in "${required[@]}"; do
        [[ -n "${!name:-}" ]] || backfill_die "필수 환경변수가 없습니다: ${name}"
    done
}

backfill_require_container() {
    command -v docker >/dev/null 2>&1 || backfill_die "docker 명령을 찾을 수 없습니다."
    local health
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$BACKFILL_CONTAINER" 2>/dev/null || true)"
    [[ "$health" == "healthy" ]] \
        || backfill_die "TimescaleDB 컨테이너가 healthy 상태가 아닙니다: ${BACKFILL_CONTAINER} (${health:-없음})"
}

backfill_psql() {
    docker exec "$BACKFILL_CONTAINER" \
        psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" "$@"
}

backfill_count_targets() {
    [[ -f "$BACKFILL_SEED_FILE" ]] || backfill_die "백필 대상 seed 파일이 없습니다: ${BACKFILL_SEED_FILE}"
    awk "/^[[:space:]]*\('/ { count += 1 } END { print count + 0 }" "$BACKFILL_SEED_FILE"
}

backfill_calculate_plan() {
    local plan
    plan="$("$BACKFILL_PYTHON_BIN" - "$BACKFILL_START_DATE" "$BACKFILL_END_DATE" <<'PY'
import sys
from datetime import datetime

from src.collector.raw.domestic_stock.holiday_calendar_collector import HolidayCalendarCollector
from src.collector.raw.kis_client import KISClient
from src.service.kis_trading_calendar import KisTradingCalendar

start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
calendar = KisTradingCalendar(
    HolidayCalendarCollector(KISClient()),
    call_interval_seconds=1.0,
)
open_dates = calendar.open_dates(start_date, end_date)
print(f"OPEN_DAY_COUNT={len(open_dates)}")
print(f"FIRST_OPEN_DATE={open_dates[0] if open_dates else ''}")
print(f"LAST_OPEN_DATE={open_dates[-1] if open_dates else ''}")
PY
)"
    printf '%s\n' "$plan"

    BACKFILL_OPEN_DAY_COUNT="$(awk -F= '/^OPEN_DAY_COUNT=/{print $2}' <<<"$plan")"
    [[ "$BACKFILL_OPEN_DAY_COUNT" =~ ^[0-9]+$ && "$BACKFILL_OPEN_DAY_COUNT" -gt 0 ]] \
        || backfill_die "KIS 휴장일 API에서 거래일 수를 계산하지 못했습니다."

    BACKFILL_TARGET_COUNT="$(backfill_count_targets)"
    [[ "$BACKFILL_TARGET_COUNT" == "6" ]] \
        || backfill_die "백필 대상 종목 수가 6개가 아닙니다: ${BACKFILL_TARGET_COUNT}"

    BACKFILL_EXPECTED_SEGMENTS=$((BACKFILL_OPEN_DAY_COUNT * BACKFILL_TARGET_COUNT))
    backfill_log "거래일 수=${BACKFILL_OPEN_DAY_COUNT}, 대상 종목 수=${BACKFILL_TARGET_COUNT}, 예상 세그먼트 수=${BACKFILL_EXPECTED_SEGMENTS}"
    backfill_log "예상 주식 API 호출 수(000660 스모크 3회 기준)=$((BACKFILL_EXPECTED_SEGMENTS * 3))"
    backfill_log "예상 주식 API 호출 수(분봉 120행 기준 보수적 상한)=$((BACKFILL_EXPECTED_SEGMENTS * 4))"
}

backfill_print_dry_run_plan() {
    BACKFILL_TARGET_COUNT="$(backfill_count_targets)"
    backfill_log "dry-run: 실제 KIS 휴장일 API, DB DDL, 백필 API 호출 및 INSERT를 실행하지 않습니다."
    backfill_log "기간=${BACKFILL_START_DATE}~${BACKFILL_END_DATE}, 대상 종목 수=${BACKFILL_TARGET_COUNT}, 요청 간격=${BACKFILL_REQUEST_INTERVAL}초"
    backfill_log "실행 예정: 새 백필 작업 생성, KRX 1분봉 전체 수집, 10초 상태 요약, 완료 검증"
}

backfill_wait_for_new_job() {
    local baseline_job_id="$1"
    local candidate=""
    local attempt
    for attempt in {1..90}; do
        candidate="$(backfill_psql -At -c "
            SELECT job_id
            FROM backfill_job
            WHERE job_id > ${baseline_job_id}
              AND job_type = 'STOCK_MINUTE_KRX'
            ORDER BY job_id DESC
            LIMIT 1;
        " 2>/dev/null || true)"
        if [[ "$candidate" =~ ^[0-9]+$ ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
        sleep 1
    done
    return 1
}

backfill_print_resume_command() {
    [[ -n "$BACKFILL_JOB_ID" ]] || return 0
    echo "재개 명령: ${BACKFILL_PROJECT_ROOT}/scripts/backfill/resume_stock_minute_backfill.sh ${BACKFILL_JOB_ID}"
}

backfill_on_interrupt() {
    echo "중단 신호를 받았습니다."
    if [[ -n "$BACKFILL_WORKER_PID" ]] && kill -0 "$BACKFILL_WORKER_PID" 2>/dev/null; then
        kill -TERM "$BACKFILL_WORKER_PID" 2>/dev/null || true
        wait "$BACKFILL_WORKER_PID" 2>/dev/null || true
    fi
    backfill_print_resume_command
    exit 130
}

backfill_install_interrupt_trap() {
    trap backfill_on_interrupt INT TERM
}

backfill_assert_job_id() {
    local job_id="$1"
    [[ "$job_id" =~ ^[0-9]+$ ]] || {
        echo "오류: job_id는 숫자만 사용할 수 있습니다." >&2
        return 1
    }
}

backfill_print_status() {
    [[ -n "$BACKFILL_JOB_ID" ]] || return 0
    local job_id="$BACKFILL_JOB_ID"
    backfill_assert_job_id "$job_id" || return 1
    backfill_psql -P pager=off -c "
        SELECT job_id, status, start_date, end_date, started_at, completed_at, failure_message
        FROM backfill_job
        WHERE job_id = ${job_id};

        SELECT
            status,
            count(*) AS segment_count,
            sum(request_count) AS api_call_count,
            sum(returned_count) AS fetched_count,
            sum(inserted_count) AS inserted_count,
            sum(duplicate_count) AS duplicate_count
        FROM backfill_segment
        WHERE job_id = ${job_id}
        GROUP BY status
        ORDER BY status;

        SELECT
            instrument_code,
            count(*) AS segment_count,
            count(*) FILTER (WHERE status = 'COMPLETED') AS completed_count,
            count(*) FILTER (WHERE status = 'FAILED') AS failed_count,
            sum(request_count) AS api_call_count,
            sum(returned_count) AS fetched_count,
            sum(inserted_count) AS inserted_count,
            sum(duplicate_count) AS duplicate_count,
            min(minimum_bar_time) AS first_bar_time,
            max(maximum_bar_time) AS last_bar_time
        FROM backfill_segment
        WHERE job_id = ${job_id}
        GROUP BY instrument_code
        ORDER BY instrument_code;
    "
}

backfill_monitor_worker() {
    local worker_exit=0
    local status_failed=0
    while kill -0 "$BACKFILL_WORKER_PID" 2>/dev/null; do
        if ! backfill_print_status; then
            echo "경고: 실행 상태 SQL 조회에 실패했습니다. 백필 프로세스는 계속 대기합니다." >&2
            status_failed=1
        fi
        sleep "$BACKFILL_MONITOR_INTERVAL"
    done

    set +e
    wait "$BACKFILL_WORKER_PID"
    worker_exit=$?
    set -e

    if ! backfill_print_status; then
        echo "오류: 최종 상태 SQL 조회에 실패했습니다." >&2
        status_failed=1
    fi

    if (( worker_exit != 0 )); then
        echo "백필 실행이 실패했습니다. 작업 로그: ${BACKFILL_WORKER_LOG_FILE}" >&2
        tail -n 50 "$BACKFILL_WORKER_LOG_FILE" || true
        backfill_print_resume_command
        return "$worker_exit"
    fi
    if (( status_failed != 0 )); then
        echo "오류: 상태 SQL 검증 실패로 자동 완료 처리하지 않습니다." >&2
        backfill_print_resume_command
        return 1
    fi
}

backfill_run_worker() {
    local mode="$1"
    shift
    "$BACKFILL_PYTHON_BIN" "$BACKFILL_RUNNER" "$@" >"$BACKFILL_WORKER_LOG_FILE" 2>&1 &
    BACKFILL_WORKER_PID=$!
    backfill_log "백필 프로세스를 시작했습니다: PID=${BACKFILL_WORKER_PID}, mode=${mode}"
}

backfill_verify_job() {
    [[ -n "$BACKFILL_JOB_ID" ]] || backfill_die "검증할 job_id가 없습니다."
    local job_id="$BACKFILL_JOB_ID"
    backfill_assert_job_id "$job_id" || backfill_die "검증할 job_id가 유효하지 않습니다."
    backfill_log "완료 검증을 시작합니다: job_id=${BACKFILL_JOB_ID}"

    if ! backfill_psql -P pager=off -c "
        SELECT
            instrument_code,
            count(*) AS expected_trade_days,
            count(*) FILTER (WHERE status = 'COMPLETED') AS completed_trade_days,
            count(*) FILTER (WHERE status = 'FAILED') AS failed_trade_days,
            count(*) FILTER (WHERE status = 'PENDING') AS pending_trade_days,
            sum(request_count) AS api_call_count,
            sum(returned_count) AS fetched_count,
            sum(inserted_count) AS inserted_count,
            sum(duplicate_count) AS duplicate_count,
            min(minimum_bar_time) AS first_bar_time,
            max(maximum_bar_time) AS last_bar_time
        FROM backfill_segment
        WHERE job_id = ${job_id}
        GROUP BY instrument_code
        ORDER BY instrument_code;

        SELECT
            segment.instrument_code,
            segment.trade_date,
            segment.status,
            segment.returned_count,
            segment.inserted_count,
            count(raw.bar_time) AS stored_bar_count
        FROM backfill_segment AS segment
        LEFT JOIN raw_stock_minute AS raw
          ON raw.stock_code = segment.instrument_code
         AND raw.bar_time::date = segment.trade_date
         AND raw.trading_venue = 'KRX'
         AND raw.collect_cycle = '1MIN'
        WHERE segment.job_id = ${job_id}
        GROUP BY
            segment.instrument_code,
            segment.trade_date,
            segment.status,
            segment.returned_count,
            segment.inserted_count
        HAVING segment.status = 'COMPLETED' AND count(raw.bar_time) = 0
        ORDER BY segment.instrument_code, segment.trade_date;

        WITH expected_segments AS (
            SELECT instrument_code, trade_date
            FROM backfill_segment
            WHERE job_id = ${job_id} AND status = 'COMPLETED'
        ), bars AS (
            SELECT raw.stock_code, raw.bar_time,
                   lag(raw.bar_time) OVER (
                       PARTITION BY raw.stock_code, raw.bar_time::date
                       ORDER BY raw.bar_time
                   ) AS previous_bar_time
            FROM raw_stock_minute AS raw
            JOIN expected_segments AS segment
              ON segment.instrument_code = raw.stock_code
             AND segment.trade_date = raw.bar_time::date
            WHERE raw.trading_venue = 'KRX' AND raw.collect_cycle = '1MIN'
        )
        SELECT stock_code, previous_bar_time, bar_time,
               extract(epoch FROM (bar_time - previous_bar_time)) / 60 AS missing_minutes
        FROM bars
        WHERE previous_bar_time IS NOT NULL
          AND bar_time - previous_bar_time > INTERVAL '1 minute'
        ORDER BY stock_code, previous_bar_time;

        WITH expected_segments AS (
            SELECT instrument_code, trade_date
            FROM backfill_segment
            WHERE job_id = ${job_id} AND status = 'COMPLETED'
        )
        SELECT raw.stock_code, raw.bar_time::date AS trade_date,
               min(raw.bar_time) AS first_bar_time, max(raw.bar_time) AS last_bar_time
        FROM raw_stock_minute AS raw
        JOIN expected_segments AS segment
          ON segment.instrument_code = raw.stock_code
         AND segment.trade_date = raw.bar_time::date
        WHERE raw.trading_venue = 'KRX' AND raw.collect_cycle = '1MIN'
        GROUP BY raw.stock_code, raw.bar_time::date
        HAVING min(raw.bar_time)::time > TIME '09:00:00'
            OR max(raw.bar_time)::time < TIME '15:30:00'
        ORDER BY raw.stock_code, trade_date;

        WITH expected_segments AS (
            SELECT instrument_code, trade_date
            FROM backfill_segment
            WHERE job_id = ${job_id} AND status = 'COMPLETED'
        )
        SELECT
            raw.stock_code,
            count(*) AS stored_count,
            count(DISTINCT (raw.bar_time, raw.data_source, raw.market_code, raw.trading_venue, raw.collect_cycle, raw.stock_code)) AS distinct_primary_key_count,
            count(*) FILTER (WHERE trading_venue = 'KRX') AS krx_count,
            count(*) FILTER (WHERE collect_cycle = '1MIN') AS one_minute_count,
            count(*) FILTER (
                WHERE raw_payload ? 'stck_bsop_date'
                  AND raw_payload ? 'stck_cntg_hour'
                  AND raw_payload ? 'stck_prpr'
            ) AS payload_preserved_count
        FROM raw_stock_minute AS raw
        JOIN expected_segments AS segment
          ON segment.instrument_code = raw.stock_code
         AND segment.trade_date = raw.bar_time::date
        WHERE raw.trading_venue = 'KRX'
          AND raw.collect_cycle = '1MIN'
        GROUP BY raw.stock_code
        ORDER BY raw.stock_code;

        WITH expected_segments AS (
            SELECT instrument_code, trade_date
            FROM backfill_segment
            WHERE job_id = ${job_id} AND status = 'COMPLETED'
        )
        SELECT
            raw.stock_code,
            raw.bar_time,
            raw.data_source,
            raw.market_code,
            raw.trading_venue,
            raw.collect_cycle,
            count(*) AS duplicate_primary_key_count
        FROM raw_stock_minute AS raw
        JOIN expected_segments AS segment
          ON segment.instrument_code = raw.stock_code
         AND segment.trade_date = raw.bar_time::date
        GROUP BY
            raw.stock_code,
            raw.bar_time,
            raw.data_source,
            raw.market_code,
            raw.trading_venue,
            raw.collect_cycle
        HAVING count(*) > 1
        ORDER BY raw.stock_code, raw.bar_time;

        WITH expected_segments AS (
            SELECT instrument_code, trade_date
            FROM backfill_segment
            WHERE job_id = ${job_id} AND status = 'COMPLETED'
        )
        SELECT
            raw.stock_code,
            raw.trading_venue,
            raw.collect_cycle,
            count(*) AS invalid_raw_count
        FROM raw_stock_minute AS raw
        JOIN expected_segments AS segment
          ON segment.instrument_code = raw.stock_code
         AND segment.trade_date = raw.bar_time::date
        WHERE raw.trading_venue <> 'KRX' OR raw.collect_cycle <> '1MIN'
        GROUP BY raw.stock_code, raw.trading_venue, raw.collect_cycle
        ORDER BY raw.stock_code, raw.trading_venue, raw.collect_cycle;
    "; then
        echo "오류: 완료 검증 SQL 실행에 실패했습니다. 작업 로그와 DB 상태를 확인하세요." >&2
        return 1
    fi

    local critical_status
    critical_status="$(backfill_psql -At -c "
        WITH segments AS (
            SELECT * FROM backfill_segment WHERE job_id = ${job_id}
        ), missing_raw AS (
            SELECT 1
            FROM segments AS segment
            LEFT JOIN raw_stock_minute AS raw
              ON raw.stock_code = segment.instrument_code
             AND raw.bar_time::date = segment.trade_date
             AND raw.trading_venue = 'KRX'
             AND raw.collect_cycle = '1MIN'
            WHERE segment.status = 'COMPLETED'
            GROUP BY segment.segment_id
            HAVING count(raw.bar_time) = 0
        ), invalid_raw AS (
            SELECT 1
            FROM raw_stock_minute AS raw
            JOIN segments AS segment
              ON segment.instrument_code = raw.stock_code
             AND segment.trade_date = raw.bar_time::date
            WHERE segment.status = 'COMPLETED'
              AND (raw.trading_venue <> 'KRX' OR raw.collect_cycle <> '1MIN'
                   OR NOT (raw.raw_payload ? 'stck_bsop_date'
                           AND raw.raw_payload ? 'stck_cntg_hour'
                           AND raw.raw_payload ? 'stck_prpr'))
        )
        SELECT CASE
            WHEN EXISTS (SELECT 1 FROM segments WHERE status IN ('PENDING', 'FAILED', 'RUNNING')) THEN 'INCOMPLETE'
            WHEN EXISTS (SELECT 1 FROM missing_raw) THEN 'MISSING_RAW'
            WHEN EXISTS (SELECT 1 FROM invalid_raw) THEN 'INVALID_RAW'
            ELSE 'PASS'
        END;
    ")" || {
        echo "오류: 완료 검증 판정 SQL 실행에 실패했습니다." >&2
        return 1
    }
    if [[ "$critical_status" != "PASS" ]]; then
        echo "오류: 완료 검증 판정 실패=${critical_status}" >&2
        return 1
    fi
    backfill_log "완료 검증 판정: PASS"
}

backfill_read_resume_job() {
    local requested_job_id="$1"
    backfill_assert_job_id "$requested_job_id" || backfill_die "job_id는 양의 정수여야 합니다."
    [[ "$requested_job_id" != "1" ]] || backfill_die "기존 스모크 작업 job_id=1은 재개하지 않습니다."

    local metadata
    metadata="$(backfill_psql -At -F '|' -c "
        SELECT job_type, start_date, end_date, status
        FROM backfill_job
        WHERE job_id = ${requested_job_id};
    ")" || backfill_die "기존 백필 작업 상태 조회에 실패했습니다."
    [[ -n "$metadata" ]] || backfill_die "존재하지 않는 job_id입니다: ${requested_job_id}"

    local job_type job_start job_end job_status
    IFS='|' read -r job_type job_start job_end job_status <<< "$metadata"
    [[ "$job_type" == "STOCK_MINUTE_KRX" ]] || backfill_die "KRX 주식·ETF 1분봉 작업이 아닙니다: ${job_type}"

    BACKFILL_JOB_ID="$requested_job_id"
    BACKFILL_START_DATE="$job_start"
    BACKFILL_END_DATE="$job_end"
    backfill_log "재개 대상: job_id=${BACKFILL_JOB_ID}, 상태=${job_status}, 기간=${BACKFILL_START_DATE}~${BACKFILL_END_DATE}"
}

backfill_bootstrap() {
    local mode="$1"
    backfill_setup_log "$mode"
    cd "$BACKFILL_PROJECT_ROOT"
    backfill_load_env
    backfill_activate_venv
    backfill_require_environment
    backfill_require_container
    backfill_install_interrupt_trap
    backfill_log "프로젝트 루트=${BACKFILL_PROJECT_ROOT}"
    backfill_log "DB=${DB_NAME}, 컨테이너=${BACKFILL_CONTAINER}, 요청 간격=${BACKFILL_REQUEST_INTERVAL}초"
}

backfill_bootstrap_database_only() {
    local mode="$1"
    backfill_setup_log "$mode"
    cd "$BACKFILL_PROJECT_ROOT"
    backfill_load_env
    backfill_activate_venv
    backfill_require_database_environment
    backfill_require_container
    backfill_log "프로젝트 루트=${BACKFILL_PROJECT_ROOT}"
    backfill_log "DB 검증 전용 실행: DB=${DB_NAME}, 컨테이너=${BACKFILL_CONTAINER}"
}
