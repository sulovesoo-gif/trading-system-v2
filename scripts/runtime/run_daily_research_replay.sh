#!/usr/bin/env bash
# Serialized rollback-only oracle replay; writes one JSON result per complete RAW day.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
set -a
. ./.env
set +a
: > /tmp/research-replay-daily.log
while read -r day; do
  timeout 90s env PYTHONPATH=. venv/bin/python scripts/runtime/exact_replay_research_master_rollback.py "$day" >> /tmp/research-replay-daily.log 2>&1 || true
  echo "DAY_DONE $day" >> /tmp/research-replay-daily.log
done < /tmp/research-raw-dates.txt
echo done > /tmp/research-replay-daily.done
