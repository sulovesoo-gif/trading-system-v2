#!/usr/bin/env bash
# 중단·실패한 KRX 주식·ETF 1분봉 백필 작업 재개기.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_stock_minute_backfill_common.sh
source "${SCRIPT_DIR}/_stock_minute_backfill_common.sh"

usage() {
    echo "사용법: $0 <job_id> [--dry-run]"
}

[[ $# -ge 1 && $# -le 2 ]] || { usage >&2; exit 2; }
requested_job_id="$1"
[[ "$requested_job_id" =~ ^[0-9]+$ ]] || { echo "오류: job_id는 양의 정수여야 합니다." >&2; exit 2; }
[[ "$requested_job_id" != "1" ]] || { echo "오류: 기존 스모크 작업 job_id=1은 재개하지 않습니다." >&2; exit 2; }

dry_run=0
if [[ $# -eq 2 ]]; then
    [[ "$2" == "--dry-run" ]] || { usage >&2; exit 2; }
    dry_run=1
fi

backfill_bootstrap "resume-stock-minute"

if (( dry_run )); then
    backfill_log "dry-run: job_id=${requested_job_id}의 상태 조회·재개·API 호출·DB 변경을 실행하지 않습니다."
    exit 0
fi

backfill_read_resume_job "$requested_job_id"

remaining_count="$(backfill_psql -At -v job_id="$BACKFILL_JOB_ID" -c "
    SELECT count(*)
    FROM backfill_segment
    WHERE job_id = :'job_id'::bigint
      AND status IN ('PENDING', 'RUNNING', 'FAILED');
")" || backfill_die "재개 대상 세그먼트 수 조회에 실패했습니다."

if [[ "$remaining_count" == "0" ]]; then
    backfill_log "재개할 PENDING·RUNNING·FAILED 세그먼트가 없습니다. 완료 검증만 실행합니다."
    backfill_verify_job
    exit 0
fi

backfill_log "재개 대상 세그먼트 수=${remaining_count}"
backfill_run_worker "resume" \
    --start-date "$BACKFILL_START_DATE" \
    --end-date "$BACKFILL_END_DATE" \
    --resume "$BACKFILL_JOB_ID" \
    --request-interval "$BACKFILL_REQUEST_INTERVAL"

backfill_monitor_worker
backfill_verify_job
