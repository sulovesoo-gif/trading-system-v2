"""Apply the additive Daily MA V0.3 PAPER runtime schema to TEST only."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.repository.database import DatabaseSettings


MIGRATION = ROOT / "database" / "migrations" / "20260824_daily_strategy_ma_v03_paper_runtime_schema.sql"


def main() -> int:
    load_dotenv(ROOT / ".env")
    if os.getenv("APPLY_DAILY_MA_V03_PAPER_RUNTIME_SCHEMA") != "YES":
        raise SystemExit("set APPLY_DAILY_MA_V03_PAPER_RUNTIME_SCHEMA=YES")
    settings = DatabaseSettings.from_environment()
    if settings.name != "trading_system_v2_test":
        raise SystemExit("PAPER runtime schema apply is restricted to trading_system_v2_test")
    import psycopg
    with psycopg.connect(**settings.connection_kwargs()) as connection, connection.cursor() as cursor:
        cursor.execute(MIGRATION.read_text(encoding="utf-8"))
        connection.commit()
    print("APPLIED daily-ma-v03 PAPER runtime additive schema to TEST")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
