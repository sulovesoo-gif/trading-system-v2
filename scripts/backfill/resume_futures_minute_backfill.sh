#!/usr/bin/env bash
# 중단·실패한 KOSPI200 선물 1분봉 백필 재개기.

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_futures_minute_backfill_common.sh
source "${SCRIPT_DIR}/_futures_minute_backfill_common.sh"

[[ $# -eq 1 ]] || { echo "사용법: $0 <job_id>" >&2; exit 2; }
job_id="$1"
futures_assert_job_id "$job_id" || exit 2
futures_bootstrap
job_type="$(futures_psql -At -c "SELECT job_type FROM backfill_job WHERE job_id = ${job_id};")"
[[ "$job_type" == "FUTURES_MINUTE_KRX" ]] || futures_die "선물 백필 job_id가 아닙니다: ${job_id}"
python "$FUTURES_BACKFILL_RUNNER" --resume "$job_id" --request-interval "$FUTURES_BACKFILL_INTERVAL"
futures_verify_job "$job_id"
