"""Apply only the non-destructive research schema to an explicitly marked test DB."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings, create_connection_pool


def main() -> int:
    load_dotenv(ROOT / ".env")
    if os.getenv("DB_INTEGRATION_TEST") != "1" or "test" not in os.getenv("DB_NAME", "").lower():
        raise RuntimeError("research DDL is restricted to DB_INTEGRATION_TEST=1 test databases")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute((ROOT / "database" / "ddl" / "26_research_strategy.sql").read_text(encoding="utf-8"))
        print("research DDL applied")
        return 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
