"""Compare every enabled Research master row with a rollback-only SQL oracle run."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
from src.research_core.engine import ResearchMasterCore
from src.research_core.registry import PostgresResearchMasterRegistry
from src.strategy_core.bars import CompletedBar


def _load_bars(cursor, stock_codes, date_from: date, date_to: date):
    """Preload each relevant instrument once; exact replay must not do 802×N queries."""
    cache: dict[tuple[str, date], tuple[CompletedBar, ...]] = {}
    dates: dict[str, list[date]] = {}
    for stock_code in sorted(stock_codes):
        cursor.execute("""SELECT bar_time,open_price,high_price,low_price,close_price,volume
                            FROM raw_stock_minute
                           WHERE stock_code=%s AND collect_cycle='1MIN' AND trading_venue='INTEGRATED'
                             AND bar_time::date BETWEEN %s AND %s ORDER BY bar_time""", (stock_code, date_from, date_to))
        grouped = defaultdict(list)
        for row in cursor.fetchall():
            grouped[row[0].date()].append(CompletedBar(row[0], *(float(value) for value in row[1:])))
        dates[stock_code] = sorted(grouped)
        for trading_date, bars in grouped.items():
            cache[stock_code, trading_date] = tuple(bars)
    return cache, dates


def _iso(value):
    return value.isoformat() if value is not None else None


def main() -> int:
    import psycopg
    from psycopg_pool import ConnectionPool

    parser = argparse.ArgumentParser()
    parser.add_argument("date_from", type=date.fromisoformat)
    parser.add_argument("date_to", type=date.fromisoformat, nargs="?", help="inclusive; defaults to date_from")
    args = parser.parse_args(); date_to = args.date_to or args.date_from
    load_dotenv(ROOT / ".env"); settings = DatabaseSettings.from_environment()
    pool = ConnectionPool(kwargs=settings.connection_kwargs(), min_size=1, max_size=1)
    try:
        definitions = PostgresResearchMasterRegistry(pool).definitions()
        with psycopg.connect(**settings.connection_kwargs()) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("CALL run_strategy_master_backtest(%s,%s,%s)", (args.date_from, date_to, 10_000_000))
                    cursor.execute("SELECT max(run_id) FROM research_backtest_run"); run_id = cursor.fetchone()[0]
                    cursor.execute("SELECT strategy_id,signal_time,entry_target_time FROM research_backtest_signal WHERE run_id=%s ORDER BY strategy_id,signal_time", (run_id,))
                    expected_entry = defaultdict(list)
                    for strategy_id, signal, target in cursor.fetchall(): expected_entry[int(strategy_id)].append((_iso(signal), _iso(target)))
                    cursor.execute("SELECT strategy_id,signal_time,entry_time,exit_time,exit_reason FROM research_backtest_trade WHERE run_id=%s ORDER BY strategy_id,signal_time", (run_id,))
                    expected_exit = defaultdict(list)
                    for strategy_id, signal, entry, exit_time, reason in cursor.fetchall(): expected_exit[int(strategy_id)].append((_iso(signal), _iso(entry), _iso(exit_time), reason))
                    codes = {definition.signal_stock_code for definition in definitions} | {definition.execution_stock_code for definition in definitions}
                    cache, dates_by_stock = _load_bars(cursor, codes, args.date_from, date_to)
                    core = ResearchMasterCore(); mismatches = []; entries_checked = exits_checked = 0
                    for definition in definitions:
                        dates = dates_by_stock[definition.signal_stock_code]
                        actual_entries = []
                        for trading_date in dates:
                            source = cache[definition.signal_stock_code, trading_date]
                            execution = cache.get((definition.execution_stock_code, trading_date), ())
                            actual_entries.extend((item, source, execution) for item in core.entries(definition, source))
                        actual_entry = [(item.signal_time.isoformat(), _iso(item.target_time)) for item, _source, _execution in actual_entries]
                        expected = expected_entry[int(definition.strategy_id)]
                        entries_checked += 1
                        if actual_entry != expected:
                            mismatches.append({"kind": "ENTRY", "strategy_id": definition.strategy_id, "expected": expected, "actual": actual_entry})
                            continue
                        actual_exit = []
                        for item, source, execution in actual_entries:
                            result = core.exit(definition, item, source, execution)
                            if result and result.target_time:
                                actual_exit.append((item.signal_time.isoformat(), _iso(item.target_time), _iso(result.target_time), result.exit_reason))
                        expected = expected_exit[int(definition.strategy_id)]
                        if expected:
                            exits_checked += 1
                            if actual_exit != expected:
                                mismatches.append({"kind": "EXIT", "strategy_id": definition.strategy_id, "expected": expected, "actual": actual_exit})
                print(json.dumps({"date_from": args.date_from.isoformat(), "date_to": date_to.isoformat(), "run_id": run_id, "rows": len(definitions), "entries_checked": entries_checked, "exits_checked": exits_checked, "mismatch_count": len(mismatches), "mismatches": mismatches[:20]}, ensure_ascii=False, sort_keys=True))
                return 0 if not mismatches else 3
            finally:
                connection.rollback()
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
