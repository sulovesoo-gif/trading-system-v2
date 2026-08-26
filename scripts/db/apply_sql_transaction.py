"""Apply one trusted repository SQL file with psycopg, optionally rollback-only."""
from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from src.repository.database import DatabaseSettings


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("sql",type=Path)
    parser.add_argument("--rollback-only",action="store_true")
    args=parser.parse_args()
    load_dotenv()
    import psycopg
    settings=DatabaseSettings.from_environment()
    sql=args.sql.read_text(encoding="utf-8")
    # Migration files own their transaction boundary. Rollback-only replaces
    # only the final terminator and therefore exercises the complete DDL.
    if args.rollback_only:
        head,separator,tail=sql.rpartition("COMMIT;")
        if not separator or tail.strip():
            raise SystemExit("SQL must end in a single COMMIT; for rollback-only validation")
        sql=head+"ROLLBACK;"
    with psycopg.connect(**settings.connection_kwargs()) as connection:
        connection.execute(sql)
    print("ROLLBACK_ONLY_PASS" if args.rollback_only else "APPLY_PASS")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
