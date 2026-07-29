#!/usr/bin/env bash
# KOSPI200 선물 1분봉 백필 DB 결과 검증기.

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_futures_minute_backfill_common.sh
source "${SCRIPT_DIR}/_futures_minute_backfill_common.sh"

[[ $# -eq 1 ]] || { echo "사용법: $0 <job_id>" >&2; exit 2; }
job_id="$1"
futures_assert_job_id "$job_id" || exit 2
futures_bootstrap_database_only
futures_verify_job "$job_id"
