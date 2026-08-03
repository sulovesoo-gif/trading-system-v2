"""Apply the non-destructive program-trade common-code seed to the test DB."""
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
        raise RuntimeError("DB_INTEGRATION_TEST=1 and a test DB are required")
    sql = (ROOT / "database" / "seed" / "03_program_collect_settings.sql").read_text(encoding="utf-8")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        with pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql)
            connection.commit()
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
