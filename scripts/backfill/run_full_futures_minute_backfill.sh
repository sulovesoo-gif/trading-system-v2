#!/usr/bin/env bash
# KOSPI200 선물 월물별 과거 1분봉 전체 백필 실행기.

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_futures_minute_backfill_common.sh
source "${SCRIPT_DIR}/_futures_minute_backfill_common.sh"

[[ $# -le 1 ]] || { echo "사용법: $0 [--dry-run]" >&2; exit 2; }
if [[ "${1:-}" == "--dry-run" ]]; then
    echo "dry-run: 기간=${FUTURES_BACKFILL_START_DATE}~${FUTURES_BACKFILL_END_DATE}, KOSPI200 선물 Manifest 기준 백필을 계획합니다."
    exit 0
fi
[[ $# -eq 0 ]] || { echo "사용법: $0 [--dry-run]" >&2; exit 2; }

futures_bootstrap
baseline="$(futures_psql -At -c "SELECT COALESCE(MAX(job_id), 0) FROM backfill_job;")"
[[ "$baseline" =~ ^[0-9]+$ ]] || futures_die "기준 job_id를 읽지 못했습니다."
python "$FUTURES_BACKFILL_RUNNER" --start-date "$FUTURES_BACKFILL_START_DATE" --end-date "$FUTURES_BACKFILL_END_DATE" --request-interval "$FUTURES_BACKFILL_INTERVAL" &
worker_pid=$!
job_id="$(futures_wait_for_job "$baseline")" || { wait "$worker_pid" || true; futures_die "새 선물 백필 job_id를 확인하지 못했습니다."; }
futures_log "새 선물 백필 작업: job_id=${job_id}"
while kill -0 "$worker_pid" 2>/dev/null; do
    futures_print_status "$job_id" || futures_die "백필 상태 조회에 실패했습니다."
    sleep "$FUTURES_BACKFILL_MONITOR_INTERVAL"
done
wait "$worker_pid"
futures_verify_job "$job_id"
