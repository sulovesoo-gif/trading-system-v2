#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

required=(DB_INTEGRATION_TEST DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "${name} must be explicitly set for the integration test." >&2
    exit 1
  fi
done

if [[ "$DB_INTEGRATION_TEST" != "1" ]]; then
  echo "DB_INTEGRATION_TEST=1 is required; refusing to run destructive DDL." >&2
  exit 1
fi

if [[ "${DB_NAME,,}" != *test* ]]; then
  echo "DB_NAME must contain 'test'; refusing to run destructive DDL." >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -m unittest test.integration.test_timescaledb_raw_repository -v
