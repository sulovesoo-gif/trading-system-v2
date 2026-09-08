"""PostgreSQL persistence for the FLOW V3 PAPER runtime."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Iterator, Sequence

from psycopg.types.json import Jsonb

from .engine import PAIR_CODE
from .models import EntrySignal, MinuteBase, MinuteState, StrategyContract

KST_OPEN = time(9, 0)
KST_SOURCE_END = time(15, 31)
EOD_SOURCE_LIMIT = time(15, 28)
EOD_EXECUTION_LIMIT = time(15, 29)


def _decimal_map(value) -> dict[int, Decimal | None]:
    value = value or {}
    return {int(key): (None if item is None else Decimal(str(item))) for key, item in value.items()}


def _cross_map(value) -> dict[str, int | None]:
    value = value or {}
    return {str(key): (None if item is None else int(item)) for key, item in value.items()}


def _json_decimal_map(value: dict[int, Decimal | None]) -> dict[str, str | None]:
    return {
        str(key): None if item is None else str(item)
        for key, item in value.items()
    }


class FlowV3PostgresRepository:
    """Keep state/event/cursor changes atomic and broker-independent."""

    consumer_code = "FLOW_V3_PAPER_RUNTIME"

    def __init__(self, pool) -> None:
        self.pool = pool

    @contextmanager
    def cycle_lock(self) -> Iterator[bool]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s))", (self.consumer_code,)
            )
            acquired = bool(cursor.fetchone()[0])
            try:
                yield acquired
            finally:
                if acquired:
                    cursor.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))", (self.consumer_code,)
                    )

    def strategies(self, *, stock_code: str) -> tuple[StrategyContract, ...]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT strategy_id,stock_code,direction,entry_family_code,
                       entry_fast_period,entry_slow_period,program_condition_code,
                       exit_fast_period,exit_slow_period,exit_policy_code,execution_code
                FROM flow_v3_strategy_master
                WHERE stock_code=%s AND is_enabled='Y'
                ORDER BY strategy_id
                """,
                (stock_code,),
            )
            return tuple(StrategyContract(*row) for row in cursor.fetchall())

    def completed_minutes(
        self, *, now: datetime, limit: int = 1000
    ) -> tuple[tuple[str, datetime], ...]:
        current_minute = now.replace(second=0, microsecond=0)
        bootstrap = datetime.combine(now.date(), KST_OPEN) - timedelta(minutes=1)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT e.stock_code,
                           date_trunc('minute',e.source_event_time)::timestamp AS bar_time,
                           c.last_bar_time
                    FROM raw_flow_execution e
                    LEFT JOIN flow_v3_runtime_cursor c
                      ON c.consumer_code=%s AND c.stock_code=e.stock_code
                    WHERE e.stock_code IN ('000660','005930')
                      AND e.source_event_time < %s
                      AND e.source_event_time::time >= TIME '09:00:00'
                      AND e.source_event_time::time < TIME '15:31:00'
                      AND date_trunc('minute',e.source_event_time)
                          > COALESCE(c.last_bar_time,%s)
                    GROUP BY e.stock_code,date_trunc('minute',e.source_event_time),c.last_bar_time
                )
                SELECT stock_code,bar_time
                FROM candidate
                ORDER BY bar_time,stock_code
                LIMIT %s
                """,
                (self.consumer_code, current_minute, bootstrap, limit),
            )
            return tuple(cursor.fetchall())

    def minute_base(self, *, stock_code: str, bar_time: datetime) -> MinuteBase:
        end = bar_time + timedelta(minutes=1)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COALESCE(sum(CASE WHEN execution_classification='1'
                       THEN COALESCE(current_price,0)::numeric*abs(COALESCE(execution_volume,0)::numeric)
                       ELSE 0 END),0),
                    COALESCE(sum(CASE WHEN execution_classification='5'
                       THEN COALESCE(current_price,0)::numeric*abs(COALESCE(execution_volume,0)::numeric)
                       ELSE 0 END),0),
                    COALESCE(sum(CASE WHEN execution_classification='1'
                       THEN COALESCE(current_price,0)::numeric*abs(COALESCE(execution_volume,0)::numeric)
                       WHEN execution_classification='5'
                       THEN -COALESCE(current_price,0)::numeric*abs(COALESCE(execution_volume,0)::numeric)
                       ELSE 0 END),0),
                    (array_agg(current_price::numeric ORDER BY source_event_time DESC,
                       receive_sequence DESC,event_index DESC)
                       FILTER (WHERE current_price IS NOT NULL))[1],
                    bool_or(COALESCE(source_gap_flag,false)),
                    count(*) FILTER (WHERE COALESCE(duplicate_flag,false))
                FROM raw_flow_execution
                WHERE stock_code=%s AND source_event_time >= %s AND source_event_time < %s
                """,
                (stock_code, bar_time, end),
            )
            buy, sell, flow, close, execution_gap, execution_dups = cursor.fetchone()
            if close is None:
                raise RuntimeError(f"No execution RAW for {stock_code} {bar_time}")

            cursor.execute(
                """
                WITH source AS (
                    SELECT *, CASE
                        WHEN abs(extract(epoch FROM (source_event_time-received_at))) >= 43200
                        THEN received_at::date + source_event_time::time
                        ELSE source_event_time END AS corrected_time
                    FROM raw_flow_program
                    WHERE stock_code=%s
                ), last_by_minute AS (
                    SELECT DISTINCT ON (date_trunc('minute',corrected_time))
                        date_trunc('minute',corrected_time)::timestamp AS minute_time,
                        COALESCE(net_buy_execution_amount,0)::numeric AS cum_net,
                        COALESCE(source_gap_flag,false) AS source_gap,
                        COALESCE(duplicate_flag,false) AS duplicate_flag
                    FROM source
                    WHERE corrected_time >= %s AND corrected_time < %s
                    ORDER BY date_trunc('minute',corrected_time),corrected_time DESC,
                             receive_sequence DESC,event_index DESC
                )
                SELECT
                    max(cum_net) FILTER (WHERE minute_time=%s),
                    max(cum_net) FILTER (WHERE minute_time=%s),
                    bool_or(source_gap) FILTER (WHERE minute_time=%s),
                    count(*) FILTER (WHERE minute_time=%s AND duplicate_flag)
                FROM last_by_minute
                """,
                (
                    stock_code,
                    bar_time - timedelta(minutes=1),
                    end,
                    bar_time,
                    bar_time - timedelta(minutes=1),
                    bar_time,
                    bar_time,
                ),
            )
            current_program, previous_program, program_gap, program_dups = cursor.fetchone()
            program_net = None
            if current_program is not None and previous_program is not None:
                program_net = current_program - previous_program

            cursor.execute(
                """
                SELECT
                    (array_agg(total_bid_quantity::numeric ORDER BY source_event_time,received_at))[1],
                    (array_agg(total_bid_quantity::numeric ORDER BY source_event_time DESC,received_at DESC))[1],
                    (array_agg(total_ask_quantity::numeric ORDER BY source_event_time,received_at))[1],
                    (array_agg(total_ask_quantity::numeric ORDER BY source_event_time DESC,received_at DESC))[1],
                    count(*),
                    bool_or(COALESCE(source_gap_flag,false)),
                    count(*) FILTER (WHERE COALESCE(duplicate_flag,false))
                FROM raw_flow_orderbook_5s
                WHERE stock_code=%s AND source_event_time >= %s AND source_event_time < %s
                """,
                (stock_code, bar_time, end),
            )
            bid_start, bid_end, ask_start, ask_end, snapshots, book_gap, book_dups = cursor.fetchone()

        return MinuteBase(
            business_date=bar_time.date(), stock_code=stock_code, bar_time=bar_time,
            underlying_close=close, aggressive_buy_amount=buy,
            aggressive_sell_amount=sell, flow_value=flow,
            program_net_flow=program_net, bid_start=bid_start, bid_end=bid_end,
            ask_start=ask_start, ask_end=ask_end, snapshot_count=snapshots,
            execution_source_gap=bool(execution_gap),
            execution_duplicate_rows=execution_dups,
            program_source_gap=bool(program_gap), program_duplicate_rows=program_dups,
            orderbook_source_gap=bool(book_gap), orderbook_duplicate_rows=book_dups,
        )

    def recent_states(
        self, *, stock_code: str, business_date: date, before: datetime, limit: int = 30
    ) -> tuple[MinuteState, ...]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT business_date,stock_code,bar_time,underlying_close,
                       aggressive_buy_amount,aggressive_sell_amount,flow_value,velocity_value,
                       program_net_flow,program_velocity,long_absorption,short_absorption,
                       snapshot_count,execution_source_gap,execution_duplicate_rows,
                       program_source_gap,program_duplicate_rows,orderbook_source_gap,
                       orderbook_duplicate_rows,quality_code,flow_averages,velocity_averages,
                       flow_crosses,velocity_crosses
                FROM flow_v3_minute_state
                WHERE stock_code=%s AND business_date=%s AND bar_time < %s
                ORDER BY bar_time DESC LIMIT %s
                """,
                (stock_code, business_date, before, limit),
            )
            rows = cursor.fetchall()
        states = [self._state_from_row(row) for row in reversed(rows)]
        return tuple(states)

    @staticmethod
    def _state_from_row(row) -> MinuteState:
        return MinuteState(
            *row[:20],
            flow_averages=_decimal_map(row[20]),
            velocity_averages=_decimal_map(row[21]),
            flow_crosses=_cross_map(row[22]),
            velocity_crosses=_cross_map(row[23]),
        )

    def persist_minute(
        self, *, state: MinuteState, entry_signals: Sequence[EntrySignal]
    ) -> tuple[int, int]:
        created = exit_signals = 0
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO flow_v3_minute_state
                (business_date,stock_code,bar_time,underlying_close,aggressive_buy_amount,
                 aggressive_sell_amount,flow_value,velocity_value,program_net_flow,program_velocity,
                 long_absorption,short_absorption,snapshot_count,execution_source_gap,
                 execution_duplicate_rows,program_source_gap,program_duplicate_rows,
                 orderbook_source_gap,orderbook_duplicate_rows,quality_code,is_complete,
                 flow_averages,velocity_averages,flow_crosses,velocity_crosses)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s)
                ON CONFLICT (stock_code,bar_time) DO UPDATE SET
                  underlying_close=EXCLUDED.underlying_close,
                  aggressive_buy_amount=EXCLUDED.aggressive_buy_amount,
                  aggressive_sell_amount=EXCLUDED.aggressive_sell_amount,
                  flow_value=EXCLUDED.flow_value,velocity_value=EXCLUDED.velocity_value,
                  program_net_flow=EXCLUDED.program_net_flow,program_velocity=EXCLUDED.program_velocity,
                  long_absorption=EXCLUDED.long_absorption,short_absorption=EXCLUDED.short_absorption,
                  snapshot_count=EXCLUDED.snapshot_count,
                  execution_source_gap=EXCLUDED.execution_source_gap,
                  execution_duplicate_rows=EXCLUDED.execution_duplicate_rows,
                  program_source_gap=EXCLUDED.program_source_gap,
                  program_duplicate_rows=EXCLUDED.program_duplicate_rows,
                  orderbook_source_gap=EXCLUDED.orderbook_source_gap,
                  orderbook_duplicate_rows=EXCLUDED.orderbook_duplicate_rows,
                  quality_code=EXCLUDED.quality_code,is_complete=EXCLUDED.is_complete,
                  flow_averages=EXCLUDED.flow_averages,velocity_averages=EXCLUDED.velocity_averages,
                  flow_crosses=EXCLUDED.flow_crosses,velocity_crosses=EXCLUDED.velocity_crosses,
                  calculated_at=CURRENT_TIMESTAMP
                """,
                (
                    state.business_date,state.stock_code,state.bar_time,state.underlying_close,
                    state.aggressive_buy_amount,state.aggressive_sell_amount,state.flow_value,
                    state.velocity_value,state.program_net_flow,state.program_velocity,
                    state.long_absorption,state.short_absorption,state.snapshot_count,
                    state.execution_source_gap,state.execution_duplicate_rows,
                    state.program_source_gap,state.program_duplicate_rows,
                    state.orderbook_source_gap,state.orderbook_duplicate_rows,
                    state.quality_code,state.is_complete,
                    Jsonb(_json_decimal_map(state.flow_averages)),
                    Jsonb(_json_decimal_map(state.velocity_averages)),
                    Jsonb(state.flow_crosses),Jsonb(state.velocity_crosses),
                ),
            )
            for signal in entry_signals:
                strategy = signal.strategy
                cursor.execute(
                    """
                    INSERT INTO flow_v3_runtime_entry_event
                    (strategy_id,entry_event_key,event_status,business_date,stock_code,direction,
                     entry_family_code,entry_fast_period,entry_slow_period,program_condition_code,
                     exit_fast_period,exit_slow_period,exit_policy_code,execution_code,
                     entry_signal_time,entry_flow_value,entry_velocity_value,entry_program_value,
                     entry_quality_code,event_context)
                    VALUES (%s,%s,'PENDING',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (strategy_id,entry_event_key) DO NOTHING
                    """,
                    (
                        strategy.strategy_id,signal.entry_event_key,state.business_date,
                        state.stock_code,strategy.direction,strategy.entry_family_code,
                        strategy.entry_fast_period,strategy.entry_slow_period,
                        strategy.program_condition_code,strategy.exit_fast_period,
                        strategy.exit_slow_period,strategy.exit_policy_code,
                        strategy.execution_code,state.bar_time,state.flow_value,
                        state.velocity_value,state.program_net_flow,state.quality_code,
                        Jsonb({
                            "velocity_lead_time": (
                                signal.velocity_lead_time.isoformat()
                                if signal.velocity_lead_time is not None
                                else None
                            )
                        }),
                    ),
                )
                created += cursor.rowcount
            if state.is_complete:
                exit_signals = self._mark_exit_signals(cursor, state)
            cursor.execute(
                """
                INSERT INTO flow_v3_runtime_cursor(consumer_code,stock_code,last_bar_time)
                VALUES (%s,%s,%s)
                ON CONFLICT (consumer_code,stock_code) DO UPDATE SET
                  last_bar_time=GREATEST(flow_v3_runtime_cursor.last_bar_time,EXCLUDED.last_bar_time),
                  updated_at=CURRENT_TIMESTAMP
                """,
                (self.consumer_code,state.stock_code,state.bar_time),
            )
        return created, exit_signals

    def _mark_exit_signals(self, cursor, state: MinuteState) -> int:
        total = 0
        for (fast, slow), pair_code in PAIR_CODE.items():
            for source, cross in (
                ("FLOW", state.flow_crosses.get(pair_code)),
                ("VELOCITY", state.velocity_crosses.get(pair_code)),
            ):
                if cross not in (-1, 1):
                    continue
                cursor.execute(
                    """
                    UPDATE flow_v3_paper_trade t
                    SET normal_exit_signal_time=%s,
                        normal_exit_flow_value=%s,
                        normal_exit_velocity_value=%s,
                        entry_quality_code=COALESCE(t.entry_quality_code,'OK'),
                        updated_at=CURRENT_TIMESTAMP
                    FROM flow_v3_strategy_master m
                    WHERE t.strategy_id=m.strategy_id
                      AND t.trade_status='OPEN'
                      AND t.normal_exit_signal_time IS NULL
                      AND t.entry_signal_time < %s
                      AND m.stock_code=%s
                      AND m.exit_fast_period=%s AND m.exit_slow_period=%s
                      AND ((%s='VELOCITY' AND m.entry_family_code='F2')
                        OR (%s='FLOW' AND m.entry_family_code IN ('F1','F3','F4')))
                      AND ((m.direction='LONG' AND %s=-1)
                        OR (m.direction='SHORT' AND %s=1))
                      AND (m.exit_policy_code='SIGNAL_HOLD'
                        OR (m.exit_policy_code='SIGNAL_EOD' AND t.trade_date=%s
                            AND %s::time<=TIME '15:28:00'))
                    """,
                    (
                        state.bar_time,state.flow_value,state.velocity_value,state.bar_time,
                        state.stock_code,fast,slow,source,source,cross,cross,
                        state.business_date,state.bar_time,
                    ),
                )
                total += cursor.rowcount
        return total

    def resolve_pending_entries(self, *, now: datetime, limit: int = 5000) -> int:
        resolved = 0
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_id,strategy_id,entry_event_key,business_date,stock_code,direction,
                       entry_family_code,entry_fast_period,entry_slow_period,program_condition_code,
                       exit_fast_period,exit_slow_period,exit_policy_code,execution_code,
                       entry_signal_time,entry_flow_value,entry_velocity_value,entry_program_value,
                       entry_quality_code
                FROM flow_v3_runtime_entry_event
                WHERE event_status='PENDING'
                ORDER BY entry_signal_time,strategy_id
                FOR UPDATE SKIP LOCKED LIMIT %s
                """,
                (limit,),
            )
            for row in cursor.fetchall():
                (event_id,strategy_id,event_key,business_date,stock_code,direction,family,
                 entry_fast,entry_slow,program_code,exit_fast,exit_slow,policy,execution_code,
                 signal_time,flow,velocity,program,quality) = row
                execution = self._execution_bar(
                    cursor, execution_code=execution_code, after=signal_time,
                    same_date=business_date,
                )
                if execution is None:
                    continue
                execution_time, execution_price = execution
                if policy == "SIGNAL_EOD" and execution_time.time() > EOD_EXECUTION_LIMIT:
                    if now.date() > business_date or now.time() >= time(15,31):
                        cursor.execute(
                            """UPDATE flow_v3_runtime_entry_event
                               SET event_status='EXPIRED',failure_reason='NO_VALID_EOD_ENTRY',
                                   updated_at=CURRENT_TIMESTAMP WHERE event_id=%s""",
                            (event_id,),
                        )
                    continue
                minute_of_day = signal_time.hour * 60 + signal_time.minute
                cursor.execute(
                    """
                    INSERT INTO flow_v3_paper_trade
                    (strategy_id,entry_event_key,trade_status,trade_date,entry_signal_time,
                     entry_minute_of_day,entry_execution_time,entry_execution_price,
                     entry_flow_value,entry_velocity_value,entry_program_value,entry_quality_code,
                     entry_context,source_system,source_detail)
                    VALUES (%s,%s,'OPEN',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'FLOW_V3',%s)
                    ON CONFLICT (strategy_id,entry_event_key) DO NOTHING
                    RETURNING paper_trade_id
                    """,
                    (
                        strategy_id,event_key,business_date,signal_time,minute_of_day,
                        execution_time,execution_price,flow,velocity,program,quality,
                        Jsonb({"entry_family_code":family,"entry_fast_period":entry_fast,
                               "entry_slow_period":entry_slow,"program_condition_code":program_code,
                               "execution_code":execution_code}),
                        Jsonb({"runtime_version":"V0.1","exit_policy_code":policy,
                               "exit_fast_period":exit_fast,"exit_slow_period":exit_slow}),
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        "SELECT paper_trade_id FROM flow_v3_paper_trade WHERE strategy_id=%s AND entry_event_key=%s",
                        (strategy_id,event_key),
                    )
                    inserted = cursor.fetchone()
                cursor.execute(
                    """UPDATE flow_v3_runtime_entry_event
                       SET event_status='EXECUTED',entry_execution_time=%s,
                           entry_execution_price=%s,paper_trade_id=%s,updated_at=CURRENT_TIMESTAMP
                       WHERE event_id=%s""",
                    (execution_time,execution_price,inserted[0],event_id),
                )
                resolved += 1
        return resolved

    def resolve_pending_exits(self, *, limit: int = 10000) -> int:
        resolved = 0
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT t.paper_trade_id,t.normal_exit_signal_time,t.entry_execution_time,
                       m.execution_code,m.exit_policy_code,t.trade_date
                FROM flow_v3_paper_trade t
                JOIN flow_v3_strategy_master m ON m.strategy_id=t.strategy_id
                WHERE t.trade_status='OPEN' AND t.normal_exit_signal_time IS NOT NULL
                  AND t.normal_exit_exec_time IS NULL
                ORDER BY t.normal_exit_signal_time,t.paper_trade_id
                FOR UPDATE OF t SKIP LOCKED LIMIT %s
                """,
                (limit,),
            )
            for trade_id,signal_time,entry_time,execution_code,policy,trade_date in cursor.fetchall():
                execution = self._execution_bar(
                    cursor,execution_code=execution_code,after=signal_time,
                    same_date=trade_date if policy == "SIGNAL_EOD" else None,
                )
                if execution is None:
                    continue
                execution_time, execution_price = execution
                cursor.execute(
                    """
                    UPDATE flow_v3_paper_trade
                    SET trade_status='CLOSED',normal_exit_exec_time=%s,normal_exit_price=%s,
                        actual_exit_time=%s,actual_exit_price=%s,exit_reason='NORMAL_EXIT',
                        holding_minutes=round(extract(epoch FROM (%s-entry_execution_time))/60.0)::integer,
                        holding_days=(%s::date-entry_execution_time::date),updated_at=CURRENT_TIMESTAMP
                    WHERE paper_trade_id=%s AND trade_status='OPEN'
                    """,
                    (execution_time,execution_price,execution_time,execution_price,
                     execution_time,execution_time,trade_id),
                )
                resolved += cursor.rowcount
        return resolved

    def finalize_eod(self, *, now: datetime) -> int:
        if now.time() < time(15,31):
            return 0
        total = 0
        business_date = now.date()
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            for stock_code in ("000660","005930"):
                cursor.execute(
                    """SELECT max(bar_time) FROM flow_v3_minute_state
                       WHERE stock_code=%s AND business_date=%s
                         AND bar_time::time<=TIME '15:28:00'""",
                    (stock_code,business_date),
                )
                source_bar = cursor.fetchone()[0]
                if source_bar is None:
                    continue
                cursor.execute(
                    """
                    SELECT DISTINCT m.execution_code
                    FROM flow_v3_paper_trade t
                    JOIN flow_v3_strategy_master m ON m.strategy_id=t.strategy_id
                    WHERE t.trade_status='OPEN' AND t.trade_date=%s AND m.stock_code=%s
                      AND m.exit_policy_code='SIGNAL_EOD'
                      AND t.normal_exit_signal_time IS NULL
                    """,
                    (business_date,stock_code),
                )
                for (execution_code,) in cursor.fetchall():
                    cursor.execute(
                        """
                        SELECT bar_time,open_price FROM raw_stock_minute
                        WHERE stock_code=%s AND trading_venue='KRX' AND collect_cycle='1MIN'
                          AND bar_time::date=%s AND bar_time::time<=TIME '15:29:00'
                          AND open_price>0
                        ORDER BY bar_time DESC,collected_at DESC LIMIT 1
                        """,
                        (execution_code,business_date),
                    )
                    execution = cursor.fetchone()
                    if execution is None:
                        continue
                    execution_time,execution_price = execution
                    cursor.execute(
                        """
                        UPDATE flow_v3_paper_trade t
                        SET trade_status='CLOSED',actual_exit_time=%s,actual_exit_price=%s,
                            exit_reason='FORCED_EOD',eod_source_bar_time=%s,
                            eod_execution_bar_time=%s,
                            holding_minutes=round(extract(epoch FROM (%s-entry_execution_time))/60.0)::integer,
                            holding_days=(%s::date-entry_execution_time::date),updated_at=CURRENT_TIMESTAMP
                        FROM flow_v3_strategy_master m
                        WHERE t.strategy_id=m.strategy_id AND t.trade_status='OPEN'
                          AND t.trade_date=%s AND m.stock_code=%s
                          AND m.execution_code=%s AND m.exit_policy_code='SIGNAL_EOD'
                          AND t.normal_exit_signal_time IS NULL
                          AND t.entry_execution_time<=%s
                        """,
                        (execution_time,execution_price,source_bar,execution_time,
                         execution_time,execution_time,business_date,stock_code,
                         execution_code,execution_time),
                    )
                    total += cursor.rowcount
        return total

    @staticmethod
    def _execution_bar(cursor, *, execution_code: str, after: datetime, same_date: date | None):
        cursor.execute(
            """
            SELECT bar_time,open_price
            FROM raw_stock_minute
            WHERE stock_code=%s AND trading_venue='KRX' AND collect_cycle='1MIN'
              AND bar_time>%s AND open_price>0
              AND (%s::date IS NULL OR bar_time::date=%s::date)
            ORDER BY bar_time,collected_at DESC LIMIT 1
            """,
            (execution_code,after,same_date,same_date),
        )
        return cursor.fetchone()
