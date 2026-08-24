"""Durable, PAPER-only repository for Daily MA V0.3.

Every write is explicit and transaction scoped.  This module deliberately has
no order/broker dependency and cannot enable a LIVE send path.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Sequence

from .contracts import SignalEvent
from .evaluator import DailyMaStrategy
from .identity import transition_key
from .identity import snapshot_hash
from .runtime import OpenDay20Trade, OpenNormalTrade


class PaperRuntimeInputMismatch(RuntimeError):
    """A previously seen raw event has a different input snapshot."""


class PostgresPaperRuntimeRepository:
    def __init__(self, pool, *, write_enabled: bool = False) -> None:
        self.pool = pool
        self.write_enabled = write_enabled

    def canonical_strategies(self) -> Sequence[DailyMaStrategy]:
        sql = """SELECT strategy_id,signal_code,execution_code,direction,
                         entry_fast_ma,entry_slow_ma,exit_fast_ma,exit_slow_ma,
                         trend_ma,day20_enabled
                    FROM vw_daily_strategy_v03_runtime
                   ORDER BY strategy_id"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        return tuple(DailyMaStrategy(str(row[0]), str(row[1]), str(row[2]), str(row[3]),
                                     int(row[4]), int(row[5]), int(row[6]), int(row[7]),
                                     int(row[8]) if row[8] is not None else None, bool(row[9]))
                     for row in rows)

    def entry_event_exists(self, strategy_id: str, event_key: str, snapshot_digest: str) -> bool:
        sql = """SELECT snapshot_hash FROM daily_strategy_paper_event
                   WHERE strategy_id=%s AND signal_event_key=%s AND event_kind='ENTRY'"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, (strategy_id, event_key))
            row = cursor.fetchone()
        if row is None:
            return False
        if str(row[0]) != snapshot_digest:
            raise PaperRuntimeInputMismatch(
                f"blocked: strategy={strategy_id} raw signal event snapshot changed")
        return True

    def record_entry(self, *, strategy: DailyMaStrategy, event: SignalEvent,
                     snapshot: dict[str, object], snapshot_digest: str,
                     execution_time: datetime | None, execution_price: float | None) -> bool:
        """CAS-create exactly one strategy/event entry; return false for duplicate."""
        if not self.write_enabled:
            return False
        try:
            from psycopg.types.json import Jsonb
        except ImportError as error:  # pragma: no cover - environment configuration
            raise RuntimeError("psycopg is required for DB writes") from error
        source_trade_key = f"{strategy.strategy_id}|{event.key()}"
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO daily_strategy_paper_event
                       (strategy_id,signal_event_key,event_kind,source_bar_time,snapshot_hash,source_snapshot,outcome)
                    VALUES (%s,%s,'ENTRY',%s,%s,%s,%s)
                    ON CONFLICT (strategy_id,signal_event_key,event_kind) DO NOTHING
                    RETURNING paper_event_id""",
                (strategy.strategy_id, event.key(), event.source_bar_time, snapshot_digest,
                 Jsonb(snapshot), "CREATED" if execution_time else "NO_EXECUTION_BAR"),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                cursor.execute("""SELECT snapshot_hash FROM daily_strategy_paper_event
                                  WHERE strategy_id=%s AND signal_event_key=%s AND event_kind='ENTRY'""",
                               (strategy.strategy_id, event.key()))
                prior = cursor.fetchone()
                if prior is None or str(prior[0]) != snapshot_digest:
                    raise PaperRuntimeInputMismatch("blocked concurrent/replay input mismatch")
                connection.commit()
                return False
            if execution_time is None or execution_price is None:
                # The event itself records this terminal no-execution outcome;
                # transition rows intentionally require a real paper_trade_id.
                connection.commit()
                return True
            cursor.execute(
                """INSERT INTO daily_strategy_trade_no_counter(strategy_id,next_trade_no)
                    VALUES (%s,1) ON CONFLICT (strategy_id) DO NOTHING""", (strategy.strategy_id,))
            cursor.execute(
                """UPDATE daily_strategy_trade_no_counter
                      SET next_trade_no=next_trade_no+1,updated_at=CURRENT_TIMESTAMP
                    WHERE strategy_id=%s RETURNING next_trade_no-1""", (strategy.strategy_id,))
            trade_no = int(cursor.fetchone()[0])
            cursor.execute(
                """INSERT INTO daily_strategy_paper_trade
                      (strategy_id,trade_no,trade_status,data_segment,return_source,
                       entry_signal_date,entry_signal_time,paper_entry_time,paper_entry_price,
                       normal_tracking_status,day20_enabled_at_entry,brake_triggered,
                       data_quality,source_system,source_trade_key,context_snapshot,source_detail)
                   VALUES (%s,%s,'OPEN','POST_LISTING_ACTUAL','DAILY_MA_V03_RUNTIME',
                           %s,%s,%s,%s,'OPEN',%s,FALSE,
                           'FULL_EXECUTION_DETAIL','DAILY_MA_V03',%s,%s,%s)
                   RETURNING paper_trade_id""",
                (strategy.strategy_id, trade_no, event.source_bar_time.date(), event.source_bar_time,
                 execution_time, execution_price, strategy.day20_enabled, source_trade_key,
                 Jsonb(snapshot), Jsonb({"signal_event_key": event.key(), "strategy": asdict(strategy)})),
            )
            paper_trade_id = int(cursor.fetchone()[0])
            cursor.execute("""UPDATE daily_strategy_paper_event SET paper_trade_id=%s
                              WHERE paper_event_id=%s""", (paper_trade_id, int(inserted[0])))
            cursor.execute(
                """INSERT INTO daily_strategy_paper_transition
                      (paper_trade_id,transition_key,transition_type,source_bar_time,
                       execution_target_time,snapshot_hash,detail)
                   VALUES (%s,%s,'ENTRY_CREATED',%s,%s,%s,%s)""",
                (paper_trade_id, transition_key(paper_trade_id, "ENTRY_CREATED", event.source_bar_time),
                 event.source_bar_time, execution_time, snapshot_digest, Jsonb(snapshot)),
            )
            connection.commit()
        return True

    def open_normal_tracking_trades(self) -> Sequence[OpenNormalTrade]:
        """Return both actual-OPEN and DAY20-actual-CLOSED normal paths."""
        sql = """SELECT p.paper_trade_id,p.entry_signal_date,m.strategy_id,m.signal_code,
                         m.execution_code,m.direction,m.entry_fast_ma,m.entry_slow_ma,
                         m.exit_fast_ma,m.exit_slow_ma,m.trend_ma,m.day20_enabled
                    FROM daily_strategy_paper_trade p
                    JOIN daily_strategy_master m ON m.strategy_id=p.strategy_id
                   WHERE p.source_system='DAILY_MA_V03'
                     AND p.normal_tracking_status='OPEN'
                   ORDER BY p.paper_trade_id"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        return tuple(OpenNormalTrade(
            int(row[0]), row[1], DailyMaStrategy(str(row[2]), str(row[3]), str(row[4]), str(row[5]),
                                                  int(row[6]), int(row[7]), int(row[8]), int(row[9]),
                                                  int(row[10]) if row[10] is not None else None, bool(row[11])))
            for row in rows)

    def record_normal_exit(self, *, paper_trade_id: int, signal_time: datetime,
                           execution_time: datetime, execution_price: float) -> bool:
        """Idempotently close normal tracking without rewriting an actual DAY20 exit."""
        if not self.write_enabled:
            return False
        try:
            from psycopg.types.json import Jsonb
        except ImportError as error:  # pragma: no cover
            raise RuntimeError("psycopg is required for DB writes") from error
        detail = {"signal_time": signal_time.isoformat(), "execution_time": execution_time.isoformat(),
                  "execution_price": execution_price}
        digest = snapshot_hash(detail)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE daily_strategy_paper_trade
                      SET normal_exit_date=%s,
                          normal_exit_time=%s,
                          normal_exit_price=%s,
                          normal_tracking_status='CLOSED',
                          normal_return_pct=CASE WHEN paper_entry_price>0
                              THEN (%s / paper_entry_price - 1.0) * 100.0 END,
                          normal_fixed_basis_pnl=CASE WHEN paper_entry_price>0
                              THEN basis_amount * (%s / paper_entry_price - 1.0) END,
                          day20_delta_return_pct=CASE WHEN paper_exit_price>0 AND paper_entry_price>0
                              THEN (%s / paper_entry_price - paper_exit_price / paper_entry_price) * 100.0 END,
                          day20_delta_fixed_basis_pnl=CASE WHEN paper_exit_price>0 AND paper_entry_price>0
                              THEN basis_amount * (%s / paper_entry_price - paper_exit_price / paper_entry_price) END,
                          actual_exit_date=CASE WHEN actual_exit_date IS NULL THEN %s ELSE actual_exit_date END,
                          paper_exit_time=CASE WHEN paper_exit_time IS NULL THEN %s ELSE paper_exit_time END,
                          paper_exit_price=CASE WHEN paper_exit_price IS NULL THEN %s ELSE paper_exit_price END,
                          trade_status=CASE WHEN actual_exit_date IS NULL THEN 'CLOSED' ELSE trade_status END,
                          exit_reason=CASE WHEN actual_exit_date IS NULL THEN 'NORMAL_MA' ELSE exit_reason END,
                          return_pct=CASE WHEN actual_exit_date IS NULL AND paper_entry_price>0
                              THEN (%s / paper_entry_price - 1.0) * 100.0 ELSE return_pct END,
                          fixed_basis_pnl=CASE WHEN actual_exit_date IS NULL AND paper_entry_price>0
                              THEN basis_amount * (%s / paper_entry_price - 1.0) ELSE fixed_basis_pnl END,
                          updated_at=CURRENT_TIMESTAMP
                    WHERE paper_trade_id=%s AND normal_tracking_status='OPEN'
                    RETURNING paper_trade_id""",
                (execution_time.date(), execution_time, execution_price, execution_price, execution_price,
                 execution_price, execution_price, execution_time.date(), execution_time, execution_price,
                 execution_price, execution_price, paper_trade_id),
            )
            updated = cursor.fetchone()
            if updated is None:
                connection.commit()
                return False
            cursor.execute(
                """INSERT INTO daily_strategy_paper_transition
                      (paper_trade_id,transition_key,transition_type,source_bar_time,
                       execution_target_time,snapshot_hash,detail)
                   VALUES (%s,%s,'NORMAL_EXIT',%s,%s,%s,%s)
                   ON CONFLICT (transition_key) DO NOTHING""",
                (paper_trade_id, transition_key(paper_trade_id, "NORMAL_EXIT", signal_time), signal_time,
                 execution_time, digest, Jsonb(detail)),
            )
            connection.commit()
        return True

    def open_day20_trades(self) -> Sequence[OpenDay20Trade]:
        sql = """SELECT p.paper_trade_id,m.strategy_id,m.signal_code,m.execution_code,m.direction,
                         m.entry_fast_ma,m.entry_slow_ma,m.exit_fast_ma,m.exit_slow_ma,m.trend_ma,m.day20_enabled
                    FROM daily_strategy_paper_trade p
                    JOIN daily_strategy_master m ON m.strategy_id=p.strategy_id
                   WHERE p.source_system='DAILY_MA_V03'
                     AND p.trade_status='OPEN' AND p.day20_enabled_at_entry=TRUE
                     AND p.day20_applied IS DISTINCT FROM TRUE
                   ORDER BY p.paper_trade_id"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
        return tuple(OpenDay20Trade(
            int(row[0]), DailyMaStrategy(str(row[1]), str(row[2]), str(row[3]), str(row[4]),
                                         int(row[5]), int(row[6]), int(row[7]), int(row[8]),
                                         int(row[9]) if row[9] is not None else None, bool(row[10])))
            for row in rows)

    def record_day20_exit(self, *, paper_trade_id: int, trigger_time: datetime,
                          execution_time: datetime, execution_price: float) -> bool:
        """Apply actual DAY20 exit once; leave normal tracking OPEN for later MA exit."""
        if not self.write_enabled:
            return False
        try:
            from psycopg.types.json import Jsonb
        except ImportError as error:  # pragma: no cover
            raise RuntimeError("psycopg is required for DB writes") from error
        detail = {"trigger_time": trigger_time.isoformat(), "execution_time": execution_time.isoformat(),
                  "execution_price": execution_price}
        digest = snapshot_hash(detail)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE daily_strategy_paper_trade
                      SET brake_triggered=TRUE,brake_trigger_time=%s,day20_applied=TRUE,
                          day20_exit_time=%s,day20_exit_price=%s,
                          actual_exit_date=%s,paper_exit_time=%s,paper_exit_price=%s,
                          trade_status='CLOSED',exit_reason='DAY20',
                          return_pct=CASE WHEN paper_entry_price>0 THEN (%s / paper_entry_price - 1.0) * 100.0 END,
                          fixed_basis_pnl=CASE WHEN paper_entry_price>0 THEN basis_amount * (%s / paper_entry_price - 1.0) END,
                          updated_at=CURRENT_TIMESTAMP
                    WHERE paper_trade_id=%s AND trade_status='OPEN'
                      AND day20_enabled_at_entry=TRUE AND day20_applied IS DISTINCT FROM TRUE
                    RETURNING paper_trade_id""",
                (trigger_time, execution_time, execution_price, execution_time.date(), execution_time,
                 execution_price, execution_price, execution_price, paper_trade_id),
            )
            updated = cursor.fetchone()
            if updated is None:
                connection.commit()
                return False
            cursor.execute(
                """INSERT INTO daily_strategy_paper_transition
                      (paper_trade_id,transition_key,transition_type,source_bar_time,
                       execution_target_time,snapshot_hash,detail)
                   VALUES (%s,%s,'DAY20_TRIGGERED',%s,%s,%s,%s)
                   ON CONFLICT (transition_key) DO NOTHING""",
                (paper_trade_id, transition_key(paper_trade_id, "DAY20_TRIGGERED", trigger_time), trigger_time,
                 execution_time, digest, Jsonb(detail)),
            )
            cursor.execute(
                """INSERT INTO daily_strategy_paper_transition
                      (paper_trade_id,transition_key,transition_type,source_bar_time,
                       execution_target_time,snapshot_hash,detail)
                   VALUES (%s,%s,'ACTUAL_EXIT',%s,%s,%s,%s)
                   ON CONFLICT (transition_key) DO NOTHING""",
                (paper_trade_id, transition_key(paper_trade_id, "ACTUAL_EXIT", trigger_time), trigger_time,
                 execution_time, digest, Jsonb(detail)),
            )
            connection.commit()
        return True
