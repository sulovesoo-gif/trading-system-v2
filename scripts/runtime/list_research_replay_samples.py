"""List one persisted historical master-backtest sample per Research family."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings


def main() -> int:
    import psycopg

    load_dotenv(ROOT / ".env")
    with psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs()) as connection, connection.cursor() as cursor:
        cursor.execute("""
            WITH ranked AS (
              SELECT m.strategy_group,t.run_id,t.strategy_id,t.trade_date,t.signal_time,
                     row_number() OVER (PARTITION BY m.strategy_group ORDER BY t.run_id DESC,t.trade_date DESC,t.signal_time DESC) AS rn
                FROM research_backtest_trade t
                JOIN research_strategy_master m ON m.strategy_id=t.strategy_id
            )
            SELECT strategy_group,run_id,strategy_id,trade_date,signal_time
              FROM ranked WHERE rn=1 ORDER BY strategy_group
        """)
        for row in cursor.fetchall():
            print("|".join(str(value) for value in row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
