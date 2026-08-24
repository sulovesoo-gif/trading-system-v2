"""TEST-only durable lifecycle fixture for the Daily MA V0.3 PAPER runtime."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.daily_ma_v03.contracts import SignalEvent
from src.daily_ma_v03.evaluator import DailyMaStrategy
from src.daily_ma_v03.repository import PostgresPaperRuntimeRepository
from src.repository.database import DatabaseSettings, create_connection_pool


FIXTURE_SOURCE_KEY = "DAILY_MA_V03|TEST_FIXTURE|DAY20_NORMAL_RECOVERY|2026-08-24"
FIXTURE_EVENT_TIME = datetime(2026, 8, 24, 15, 18)


def _strategy(pool) -> DailyMaStrategy:
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT strategy_id,signal_code,execution_code,direction,entry_fast_ma,entry_slow_ma,
                                 exit_fast_ma,exit_slow_ma,trend_ma,day20_enabled
                            FROM vw_daily_strategy_v03_runtime ORDER BY strategy_id LIMIT 1""")
        row = cursor.fetchone()
    if row is None:
        raise RuntimeError("canonical runtime strategy missing")
    return DailyMaStrategy(str(row[0]), str(row[1]), str(row[2]), str(row[3]), int(row[4]), int(row[5]),
                           int(row[6]), int(row[7]), int(row[8]) if row[8] is not None else None, bool(row[9]))


def _fixture_trade(pool, strategy: DailyMaStrategy) -> int:
    with pool.connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT paper_trade_id FROM daily_strategy_paper_trade WHERE source_trade_key=%s",
                       (FIXTURE_SOURCE_KEY,))
        existing = cursor.fetchone()
        if existing:
            connection.commit()
            return int(existing[0])
        cursor.execute("""INSERT INTO daily_strategy_trade_no_counter(strategy_id,next_trade_no)
                          VALUES (%s,1) ON CONFLICT (strategy_id) DO NOTHING""", (strategy.strategy_id,))
        cursor.execute("""UPDATE daily_strategy_trade_no_counter SET next_trade_no=next_trade_no+1,
                          updated_at=CURRENT_TIMESTAMP WHERE strategy_id=%s RETURNING next_trade_no-1""",
                       (strategy.strategy_id,))
        trade_no = int(cursor.fetchone()[0])
        cursor.execute("""INSERT INTO daily_strategy_paper_trade
                      (strategy_id,trade_no,trade_status,data_segment,return_source,entry_signal_date,
                       entry_signal_time,paper_entry_time,paper_entry_price,normal_tracking_status,
                       day20_enabled_at_entry,brake_triggered,data_quality,source_system,source_trade_key,
                       context_snapshot,source_detail)
                   VALUES (%s,%s,'OPEN','POST_LISTING_ACTUAL','DAILY_MA_V03_TEST_FIXTURE',%s,%s,%s,100,
                           'OPEN',TRUE,FALSE,'FULL_EXECUTION_DETAIL','DAILY_MA_V03',%s,'{}'::jsonb,
                           '{\"fixture\":true,\"purpose\":\"DAY20_NORMAL_RECOVERY\"}'::jsonb)
                   RETURNING paper_trade_id""",
                       (strategy.strategy_id, trade_no, FIXTURE_EVENT_TIME.date(), FIXTURE_EVENT_TIME,
                        datetime(2026, 8, 24, 9, 59), FIXTURE_SOURCE_KEY))
        result = int(cursor.fetchone()[0])
        connection.commit()
        return result


def main() -> int:
    load_dotenv(ROOT / ".env")
    if os.getenv("RUN_DAILY_MA_V03_PAPER_LIFECYCLE_FIXTURE") != "YES":
        raise SystemExit("set RUN_DAILY_MA_V03_PAPER_LIFECYCLE_FIXTURE=YES")
    settings = DatabaseSettings.from_environment()
    if settings.name != "trading_system_v2_test":
        raise SystemExit("fixture is restricted to trading_system_v2_test")
    pool = create_connection_pool(settings)
    try:
        strategy = _strategy(pool)
        repository = PostgresPaperRuntimeRepository(pool, write_enabled=True)
        # NO_EXECUTION_BAR: event only, no fabricated paper trade/price/time.
        event = SignalEvent("TEST_NO_EXEC", "LONG", 3, 5, "2026-08-24", FIXTURE_EVENT_TIME)
        no_execution_created = repository.record_entry(strategy=strategy, event=event,
            snapshot={"fixture": "NO_EXECUTION_BAR"}, snapshot_digest="a" * 64,
            execution_time=None, execution_price=None)
        no_execution_rerun = repository.record_entry(strategy=strategy, event=event,
            snapshot={"fixture": "NO_EXECUTION_BAR"}, snapshot_digest="a" * 64,
            execution_time=None, execution_price=None)
        paper_trade_id = _fixture_trade(pool, strategy)
        day20_first = repository.record_day20_exit(paper_trade_id=paper_trade_id,
            trigger_time=datetime(2026, 8, 24, 10, 0), execution_time=datetime(2026, 8, 24, 10, 1),
            execution_price=80.0)
        # New repository instance is deliberate restart/recovery coverage.
        restarted = PostgresPaperRuntimeRepository(pool, write_enabled=True)
        normal_first = restarted.record_normal_exit(paper_trade_id=paper_trade_id,
            signal_time=FIXTURE_EVENT_TIME, execution_time=datetime(2026, 8, 24, 15, 19),
            execution_price=110.0)
        with pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT trade_status,day20_applied,day20_exit_time,day20_exit_price,
                                     paper_exit_time,paper_exit_price,normal_tracking_status,
                                     normal_exit_time,normal_exit_price,day20_delta_return_pct
                                FROM daily_strategy_paper_trade WHERE paper_trade_id=%s""", (paper_trade_id,))
            row = cursor.fetchone()
            cursor.execute("""SELECT count(*) FROM daily_strategy_paper_event
                               WHERE strategy_id=%s AND signal_event_key=%s AND event_kind='ENTRY'""",
                           (strategy.strategy_id, event.key()))
            no_execution_events = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM daily_strategy_paper_transition WHERE paper_trade_id=%s",
                           (paper_trade_id,))
            transitions = cursor.fetchone()[0]
        print(json.dumps({"fixture_paper_trade_id": paper_trade_id, "no_execution_created": no_execution_created,
                          "no_execution_rerun": no_execution_rerun, "no_execution_events": no_execution_events,
                          "day20_first": day20_first, "normal_first_after_restart": normal_first,
                          "trade_status": row[0], "day20_applied": row[1], "day20_exit_time": row[2],
                          "day20_exit_price": str(row[3]), "actual_exit_time": row[4],
                          "actual_exit_price": str(row[5]), "normal_tracking_status": row[6],
                          "normal_exit_time": row[7], "normal_exit_price": str(row[8]),
                          "delta_return_pct": str(row[9]), "transitions": transitions}, default=str, sort_keys=True))
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
