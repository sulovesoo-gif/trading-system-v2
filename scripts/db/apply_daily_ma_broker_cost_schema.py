"""Apply V0.4.2 Daily MA broker-cost schema to TEST only."""
from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings

def main() -> int:
    load_dotenv(ROOT / '.env')
    if os.getenv('APPLY_DAILY_MA_BROKER_COST_SCHEMA') != 'YES':
        raise SystemExit('set APPLY_DAILY_MA_BROKER_COST_SCHEMA=YES')
    settings = DatabaseSettings.from_environment()
    if settings.name != 'trading_system_v2_test':
        raise SystemExit('broker cost schema apply is restricted to trading_system_v2_test')
    import psycopg
    with psycopg.connect(**settings.connection_kwargs()) as connection, connection.cursor() as cursor:
        cursor.execute((ROOT / 'database/migrations/20260825_daily_ma_broker_cost_allocation.sql').read_text(encoding='utf-8'))
        connection.commit()
    print('APPLIED Daily MA V0.4.2 broker cost schema to TEST')
    return 0

if __name__ == '__main__': raise SystemExit(main())
