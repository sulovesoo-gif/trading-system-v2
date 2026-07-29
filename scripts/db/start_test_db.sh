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

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required." >&2
  exit 1
fi

if [[ -z "${TIMESCALEDB_PASSWORD:-}" ]]; then
  echo "TIMESCALEDB_PASSWORD must be set before starting the test database." >&2
  exit 1
fi

docker compose up -d --wait timescaledb
docker compose ps
