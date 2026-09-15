#!/usr/bin/env python3
"""Dry-run by default; production use requires a separately approved --apply."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg
from dotenv import load_dotenv

from src.minute_ma.integrated_raw_retention import (
    PostgresRetentionStore, RetentionBlocked, run_retention,
)
from src.repository.database import DatabaseSettings


def log(event):
    print(json.dumps(event, default=str, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="drop verified cold chunks; default READ ONLY")
    parser.add_argument("--env-file", required=True, help="DB-only maintenance environment file")
    parser.add_argument("--statement-timeout-seconds", type=int, default=120)
    parser.add_argument("--grace-ms", type=int, default=2000,
                        help="must match builder finalize grace (unit default 2000ms)")
    parser.add_argument("--sql-file", type=Path,
                        help="execute an approved migration/diagnostic SQL instead of retention; requires --apply for writes")
    args = parser.parse_args()
    if args.statement_timeout_seconds < 1 or args.grace_ms < 0:
        parser.error("timeout must be positive and grace must be nonnegative")
    load_dotenv(args.env_file, override=False)
    now = datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
    try:
        with psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs()) as conn:
            if not args.apply:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            conn.execute("SET LOCAL lock_timeout='1s'")
            conn.execute("SELECT set_config('statement_timeout',%s,true)",
                         (f"{args.statement_timeout_seconds}s",))
            conn.execute("SET LOCAL idle_in_transaction_session_timeout='60s'")
            if args.sql_file:
                # Limited to checked-in retention artifacts, not an arbitrary SQL runner.
                root = Path(__file__).resolve().parents[2]
                permitted = {
                    (root / "database/migrations/20260915_minute_ma_integrated_raw_chunk_interval.sql").resolve(),
                    (root / "scripts/ops/verify_minute_ma_integrated_raw_retention_readonly.sql").resolve(),
                }
                path = args.sql_file.resolve()
                if path not in permitted:
                    raise RetentionBlocked("SQL_FILE_OUTSIDE_RETENTION_ARTIFACTS")
                cur = conn.execute(path.read_text(encoding="utf-8"), prepare=False)
                while True:
                    if cur.description:
                        names = [c.name for c in cur.description]
                        log({"sql_result": [dict(zip(names, r)) for r in cur.fetchall()]})
                    if not cur.nextset():
                        break
                result = {"status": "SQL_PASS", "file": str(path), "write_enabled": args.apply}
            else:
                result = run_retention(PostgresRetentionStore(conn, grace_ms=args.grace_ms),
                                       now=now, apply=args.apply, emit=log)
        # Success only after the transaction commit. An exception rolls all drops back.
        log(result)
        return 0
    except RetentionBlocked as error:
        log({"status": "SKIP", "run_at_kst": str(now), "reason": str(error),
             "actual_removed_chunks": [], "transaction": "ROLLED_BACK"})
        return 2
    except Exception as error:
        log({"status": "FAIL", "run_at_kst": str(now), "reason": str(error),
             "actual_removed_chunks": [], "transaction": "ROLLED_BACK"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
