"""Apply Daily MA V0.4 capital schema to TEST only with an explicit gate."""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings

MIGRATION = ROOT / "database" / "migrations" / "20260824_daily_strategy_ma_v04_capital_additive.sql"

def main() -> int:
    load_dotenv(ROOT / ".env")
    if os.getenv("APPLY_DAILY_MA_V04_CAPITAL_SCHEMA") != "YES":
        raise SystemExit("set APPLY_DAILY_MA_V04_CAPITAL_SCHEMA=YES")
    settings = DatabaseSettings.from_environment()
    if settings.name != "trading_system_v2_test":
        raise SystemExit("V0.4 capital schema apply is restricted to trading_system_v2_test")
    import psycopg
    with psycopg.connect(**settings.connection_kwargs()) as connection, connection.cursor() as cursor:
        cursor.execute(MIGRATION.read_text(encoding="utf-8"))
        connection.commit()
    print("APPLIED Daily MA V0.4 capital schema to TEST")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
