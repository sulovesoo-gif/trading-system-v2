"""Execute the historical procedure only inside a transaction that is rolled back.

This obtains a non-persistent oracle run for Research Core exact-replay work.
No research result, RAW, approval, order, or fill row is committed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings


def main() -> int:
    import psycopg

    parser = argparse.ArgumentParser()
    parser.add_argument("trading_date", type=date.fromisoformat)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    with psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs()) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("CALL run_strategy_master_backtest(%s,%s,%s)", (args.trading_date, args.trading_date, 10_000_000))
                cursor.execute("""
                    SELECT m.strategy_group,count(*)
                      FROM research_backtest_signal s
                      JOIN research_strategy_master m ON m.strategy_id=s.strategy_id
                     WHERE s.run_id=(SELECT max(run_id) FROM research_backtest_run)
                     GROUP BY m.strategy_group ORDER BY m.strategy_group
                """)
                for group, count in cursor.fetchall():
                    print(f"{group}|{count}")
                cursor.execute("SELECT count(*) FROM research_backtest_trade WHERE run_id=(SELECT max(run_id) FROM research_backtest_run)")
                print(f"trades|{cursor.fetchone()[0]}")
        finally:
            connection.rollback()
    print("ROLLED_BACK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
