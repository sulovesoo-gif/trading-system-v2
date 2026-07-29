#!/usr/bin/env bash
# KRX 주식·ETF 6개 종목의 과거 1분봉 전체 백필 실행기.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_stock_minute_backfill_common.sh
source "${SCRIPT_DIR}/_stock_minute_backfill_common.sh"

usage() {
    echo "사용법: $0 [--dry-run]"
}

dry_run=0
case "${1:-}" in
    "") ;;
    --dry-run) dry_run=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac

backfill_bootstrap "full-stock-minute"

if (( dry_run )); then
    backfill_print_dry_run_plan
    exit 0
fi

backfill_calculate_plan

baseline_job_id="$(backfill_psql -At -c "SELECT COALESCE(MAX(job_id), 0) FROM backfill_job;")"
[[ "$baseline_job_id" =~ ^[0-9]+$ ]] || backfill_die "기준 job_id를 읽지 못했습니다."

backfill_run_worker "new" \
    --start-date "$BACKFILL_START_DATE" \
    --end-date "$BACKFILL_END_DATE" \
    --request-interval "$BACKFILL_REQUEST_INTERVAL"

BACKFILL_JOB_ID="$(backfill_wait_for_new_job "$baseline_job_id")" \
    || {
        echo "오류: 새 job_id를 자동 추출하지 못했습니다." >&2
        wait "$BACKFILL_WORKER_PID" || true
        tail -n 50 "$BACKFILL_WORKER_LOG_FILE" || true
        exit 1
    }

backfill_log "새 백필 작업을 생성했습니다: job_id=${BACKFILL_JOB_ID}"
backfill_monitor_worker
backfill_verify_job
