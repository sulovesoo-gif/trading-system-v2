"""Controlled, single-process replay of today's stored multi-MA inputs.

This tool never calls KIS.  It may delete only today's multi-MA analysis
artifacts for one explicitly selected stock/venue, then rebuilds them from
the immutable completed-minute and snapshot RAW rows.  Market timestamps are
passed through unchanged; ``created_at`` remains the actual replay time.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from scripts.realtime.run_multi_ma_analysis import Runtime, _snapshot_bar
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.multi_ma_performance_repository import (
    MultiMaPerformanceKey,
    MultiMaPerformanceRepository,
    OBSERVATION_CODES,
    STRATEGY_CODES,
)


def clear_today(pool, stock_code: str, venue: str) -> None:
    """Delete only derived analysis rows; RAW and legacy SMA tables are absent."""
    with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
        scope = "trade_date=CURRENT_DATE AND stock_code=%s AND trading_venue=%s"
        cur.execute(
            "DELETE FROM analysis_multi_ma_trade_leg leg USING analysis_multi_ma_trade trade "
            f"WHERE leg.trade_id=trade.trade_id AND {scope}", (stock_code, venue)
        )
        for table in ("analysis_multi_ma_trade", "analysis_multi_ma_signal", "analysis_multi_ma_summary", "analysis_multi_ma_state"):
            cur.execute(f"DELETE FROM {table} WHERE {scope}", (stock_code, venue))


def rows(pool, stock_code: str, venue: str):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT bar_time FROM raw_stock_minute WHERE stock_code=%s AND trading_venue=%s
            AND collect_cycle='1MIN' AND bar_time::date=CURRENT_DATE ORDER BY bar_time""", (stock_code, venue)
        )
        completed = [row[0] for row in cur.fetchall()]
        cur.execute(
            """SELECT snapshot_time,target_bar_time,open_price,high_price,low_price,close_price
            FROM raw_stock_minute_snapshot WHERE stock_code=%s AND trading_venue=%s
            AND collect_cycle='5SEC' AND snapshot_time::date=CURRENT_DATE
            AND EXTRACT(SECOND FROM snapshot_time)::integer IN (5,10,15,20,25,30,35,40,45,50,55)
            ORDER BY snapshot_time""", (stock_code, venue)
        )
        snapshots = cur.fetchall()
    return completed, snapshots


def summary_counts(pool, stock_code: str, venue: str) -> dict[str, int]:
    with pool.connection() as conn, conn.cursor() as cur:
        result = {}
        for name, table in (("signals", "analysis_multi_ma_signal"), ("trades", "analysis_multi_ma_trade"), ("states", "analysis_multi_ma_state"), ("summaries", "analysis_multi_ma_summary")):
            cur.execute(f"SELECT count(*) FROM {table} WHERE trade_date=CURRENT_DATE AND stock_code=%s AND trading_venue=%s" if table != "analysis_multi_ma_state" else f"SELECT count(*) FROM {table} WHERE trade_date=CURRENT_DATE AND stock_code=%s AND trading_venue=%s", (stock_code, venue))
            result[name] = cur.fetchone()[0]
        cur.execute("""SELECT count(*) FROM analysis_multi_ma_trade_leg leg JOIN analysis_multi_ma_trade trade ON trade.trade_id=leg.trade_id
        WHERE trade_date=CURRENT_DATE AND stock_code=%s AND trading_venue=%s""", (stock_code, venue))
        result["legs"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM analysis_multi_ma_trade WHERE trade_date=CURRENT_DATE AND stock_code=%s AND trading_venue=%s AND status='OPEN'", (stock_code, venue))
        result["open"] = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM analysis_multi_ma_trade WHERE trade_date=CURRENT_DATE AND stock_code=%s AND trading_venue=%s AND status='CLOSED'", (stock_code, venue))
        result["closed"] = cur.fetchone()[0]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Required because only derived same-day rows are rebuilt.")
    parser.add_argument("--stock-code", default="000660")
    parser.add_argument("--venue", default="INTEGRATED")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if not args.apply:
        raise SystemExit("Refusing mutation without --apply.")
    if "test" not in os.getenv("DB_NAME", "").lower():
        raise SystemExit("Replay is restricted to a test database.")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        clear_today(pool, args.stock_code, args.venue)
        completed, snapshots = rows(pool, args.stock_code, args.venue)
        runtime = Runtime(pool, restore_feature_state=False)
        for bar_time in completed:
            runtime._analyze(args.stock_code, args.venue, "COMPLETE", bar_time, None)
        for snapshot_time, target_bar_time, op, hi, lo, close in snapshots:
            runtime._analyze(args.stock_code, args.venue, f"{snapshot_time.second:02d}", target_bar_time - timedelta(microseconds=1), _snapshot_bar({
                "target_bar_time": target_bar_time, "open_price": op, "high_price": hi, "low_price": lo, "close_price": close,
            }))
        config = runtime.codes.active_ma_config("MA_3_5_10")
        repository = MultiMaPerformanceRepository(pool)
        day = completed[-1].date() if completed else None
        if day is not None:
            for strategy in STRATEGY_CODES:
                for observation in OBSERVATION_CODES:
                    repository.rebuild_daily_summary(MultiMaPerformanceKey(day, args.stock_code, args.venue, strategy, observation, config.code, config.price_field), initial_capital=runtime.performance.initial_capital)
        print(summary_counts(pool, args.stock_code, args.venue))
        return 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
