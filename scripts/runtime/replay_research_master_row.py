"""Read-only exact-replay comparator for one historical Research master row.

It compares RAW -> ResearchMasterCore decisions with the latest persisted
``run_strategy_master_backtest`` artifact.  It never invokes the procedure and
never writes a run, candidate, approval, order, or fill.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from psycopg_pool import ConnectionPool

from src.repository.database import DatabaseSettings
from src.research_core.engine import ResearchMasterCore
from src.research_core.registry import PostgresResearchMasterRegistry


def _bars(pool, stock_code: str, trading_date: date):
    from src.strategy_core.bars import CompletedBar
    sql = """SELECT bar_time,open_price,high_price,low_price,close_price,volume
               FROM raw_stock_minute
              WHERE stock_code=%s AND collect_cycle='1MIN' AND trading_venue='INTEGRATED'
                AND bar_time::date=%s ORDER BY bar_time"""
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute(sql, (stock_code, trading_date)); rows = cursor.fetchall()
    return tuple(CompletedBar(row[0], *(float(value) for value in row[1:])) for row in rows)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("strategy_id", type=int); parser.add_argument("trading_date", type=date.fromisoformat)
    args = parser.parse_args(); load_dotenv(ROOT / ".env")
    pool = ConnectionPool(kwargs=DatabaseSettings.from_environment().connection_kwargs(), min_size=1, max_size=1)
    try:
        definitions = PostgresResearchMasterRegistry(pool).definitions(strategy_id=args.strategy_id)
        if len(definitions) != 1:
            raise SystemExit("strategy_id did not resolve exactly one enabled Research master row")
        definition = definitions[0]; core = ResearchMasterCore()
        source = _bars(pool, definition.signal_stock_code, args.trading_date)
        execution = _bars(pool, definition.execution_stock_code, args.trading_date)
        entries = core.entries(definition, source)
        computed = [{"signal_time": item.signal_time.isoformat(), "entry_target_time": item.target_time.isoformat() if item.target_time else None,
                     "exit": (lambda x: {"exit_time": x.target_time.isoformat() if x and x.target_time else None, "exit_reason": x.exit_reason})(core.exit(definition, item, source, execution))} for item in entries]
        sql = """SELECT s.signal_time,s.entry_target_time,t.exit_time,t.exit_reason
                   FROM research_backtest_signal s
              LEFT JOIN research_backtest_trade t ON t.run_id=s.run_id AND t.strategy_id=s.strategy_id AND t.signal_time=s.signal_time
                  WHERE s.strategy_id=%s AND s.trade_date=%s
                  ORDER BY s.run_id DESC LIMIT 1"""
        with pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, (args.strategy_id, args.trading_date)); expected = cursor.fetchone()
        result = {"strategy_id": args.strategy_id, "date": args.trading_date.isoformat(), "computed": computed,
                  "expected": [value.isoformat() if hasattr(value, "isoformat") else value for value in expected] if expected else None}
        if expected and len(computed) == 1:
            result["exact"] = [computed[0]["signal_time"], computed[0]["entry_target_time"], computed[0]["exit"]["exit_time"], computed[0]["exit"]["exit_reason"]] == [value.isoformat() if hasattr(value, "isoformat") else value for value in expected]
        else:
            result["exact"] = False
        print(json.dumps(result, ensure_ascii=False, sort_keys=True)); return 0 if result["exact"] else 3
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
