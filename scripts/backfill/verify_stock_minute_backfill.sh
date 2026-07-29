#!/usr/bin/env bash
# 완료된 KRX 주식·ETF 1분봉 백필 작업의 DB 결과만 검증한다.

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_stock_minute_backfill_common.sh
source "${SCRIPT_DIR}/_stock_minute_backfill_common.sh"

usage() {
    echo "사용법: $0 <job_id> [--dry-run]"
}

[[ $# -ge 1 && $# -le 2 ]] || { usage >&2; exit 2; }
requested_job_id="$1"
backfill_assert_job_id "$requested_job_id" || { echo "오류: job_id는 숫자만 사용할 수 있습니다." >&2; exit 2; }

dry_run=0
if [[ $# -eq 2 ]]; then
    [[ "$2" == "--dry-run" ]] || { usage >&2; exit 2; }
    dry_run=1
fi

backfill_bootstrap_database_only "verify-stock-minute"
BACKFILL_JOB_ID="$requested_job_id"

if (( dry_run )); then
    backfill_log "dry-run: job_id=${BACKFILL_JOB_ID}에 대한 DB 조회와 검증 SQL을 실행하지 않습니다."
    exit 0
fi

backfill_log "최종 백필 검증을 시작합니다: job_id=${BACKFILL_JOB_ID}"
backfill_print_status
backfill_verify_job
backfill_log "최종 백필 검증이 완료되었습니다: job_id=${BACKFILL_JOB_ID}"
