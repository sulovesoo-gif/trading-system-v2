"""Persistence for the isolated Minute MA H0UNCNT0 realtime axis."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from psycopg.types.json import Jsonb

from src.flow_raw.realtime_minute import ExecutionTick, RealtimeMinuteBar, build_realtime_minute_bars

from .integrated_realtime_contracts import (
    TR_INTEGRATED_EXECUTION,
    IntegratedExecutionEvent,
    as_decimal,
    as_int,
    integrated_source_datetime,
)


class MinuteMaIntegratedRealtimeRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def open_connection(self, *, connection_id: UUID, collector_instance_id: UUID,
                        connected_at: datetime, reconnect_flag: bool,
                        subscriptions: list[dict[str, str]]) -> None:
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO minute_ma_integrated_ws_connection(
                     connection_id,collector_instance_id,connected_at,reconnect_flag,status,subscriptions)
                   VALUES(%s,%s,%s,%s,'CONNECTED',%s)""",
                (connection_id, collector_instance_id, connected_at, reconnect_flag, Jsonb(subscriptions)),
            )

    def close_connection(self, connection_id: UUID, *, disconnected_at: datetime,
                         status: str, reason: str, last_sequence: int) -> None:
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """UPDATE minute_ma_integrated_ws_connection
                      SET disconnected_at=%s,status=%s,close_reason=%s,last_receive_sequence=%s
                    WHERE connection_id=%s""",
                (disconnected_at, status, reason, last_sequence, connection_id),
            )

    def recent_hashes(self, *, since: datetime) -> set[tuple[str, str, str]]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT tr_id,stock_code,payload_hash
                     FROM raw_minute_ma_integrated_execution WHERE received_at >= %s""",
                (since,),
            )
            return {(str(row[0]), str(row[1]), str(row[2])) for row in cursor.fetchall()}

    def save_event(self, event: IntegratedExecutionEvent, *, received_at: datetime,
                   connection_id: UUID, collector_instance_id: UUID,
                   receive_sequence: int, reconnect_flag: bool,
                   source_gap_flag: bool, event_time_regression_flag: bool,
                   duplicate_flag: bool) -> None:
        source_time = integrated_source_datetime(event, received_at=received_at)
        values = event.values
        params = (
            received_at, source_time, source_time.date(), values.get("MKSC_SHRN_ISCD"),
            connection_id, collector_instance_id, receive_sequence, event.event_index,
            reconnect_flag, source_gap_flag, event_time_regression_flag, duplicate_flag,
            event.payload_hash, as_int(values.get("STCK_PRPR")), as_int(values.get("CNTG_VOL")),
            as_int(values.get("ACML_VOL")), as_decimal(values.get("ACML_TR_PBMN")),
            Jsonb(values), event.raw_record,
        )
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO raw_minute_ma_integrated_execution(
                     received_at,source_event_time,business_date,stock_code,connection_id,
                     collector_instance_id,receive_sequence,event_index,reconnect_flag,source_gap_flag,
                     event_time_regression_flag,duplicate_flag,payload_hash,current_price,
                     execution_volume,accumulated_volume,accumulated_amount,raw_values,raw_payload)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                params,
            )

    def execution_ticks(self, *, since: datetime, until: datetime) -> tuple[ExecutionTick, ...]:
        sql = """SELECT e.stock_code,e.source_event_time,c.connected_at,e.receive_sequence,
                        e.event_index,e.received_at,e.current_price,e.execution_volume,
                        e.accumulated_volume,e.connection_id::text,e.reconnect_flag,
                        e.source_gap_flag,e.event_time_regression_flag,e.duplicate_flag
                   FROM raw_minute_ma_integrated_execution e
                   JOIN minute_ma_integrated_ws_connection c USING(connection_id)
                  WHERE e.source_event_time >= %s AND e.source_event_time < %s
                  ORDER BY e.source_event_time,c.connected_at,e.receive_sequence,e.event_index"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, (since, until))
            rows = cursor.fetchall()
        return tuple(ExecutionTick(
            stock_code=str(row[0]), source_event_time=row[1], connection_connected_at=row[2],
            receive_sequence=int(row[3]), event_index=int(row[4]), received_at=row[5],
            current_price=int(row[6]), execution_volume=None if row[7] is None else int(row[7]),
            accumulated_volume=None if row[8] is None else int(row[8]), connection_id=str(row[9]),
            reconnect_flag=bool(row[10]), source_gap_flag=bool(row[11]),
            event_time_regression_flag=bool(row[12]), duplicate_flag=bool(row[13]),
        ) for row in rows)

    def save_bar(self, bar: RealtimeMinuteBar) -> bool:
        sql = """INSERT INTO minute_ma_integrated_realtime_minute_bar(
          bar_time,stock_code,open_price,high_price,low_price,close_price,volume,
          execution_volume_sum,first_accumulated_volume,last_accumulated_volume,event_count,
          message_count,first_source_event_time,last_source_event_time,first_received_at,
          last_received_at,finalized_at,finalize_reason,watermark_delay_ms,connection_count,
          reconnect_flag,source_gap_flag,event_time_regression_flag,accumulated_volume_regression,
          ordering_invariant_failure,duplicate_excluded_count,quality_status,quality_reasons)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(bar_time,stock_code,trading_venue) DO NOTHING RETURNING 1"""
        params = (
            bar.bar_time, bar.stock_code, bar.open_price, bar.high_price, bar.low_price,
            bar.close_price, bar.volume, bar.execution_volume_sum, bar.first_accumulated_volume,
            bar.last_accumulated_volume, bar.event_count, bar.message_count,
            bar.first_source_event_time, bar.last_source_event_time, bar.first_received_at,
            bar.last_received_at, bar.finalized_at, bar.finalize_reason, bar.watermark_delay_ms,
            bar.connection_count, bar.reconnect_flag, bar.source_gap_flag,
            bar.event_time_regression_flag, bar.accumulated_volume_regression,
            bar.ordering_invariant_failure, bar.duplicate_excluded_count,
            bar.quality_status, list(bar.quality_reasons),
        )
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone() is not None

    def run_recent(self, *, now: datetime, grace_ms: int) -> int:
        start = now.replace(second=0, microsecond=0) - timedelta(minutes=3)
        bars = build_realtime_minute_bars(
            self.execution_ticks(since=start, until=now + timedelta(seconds=1)),
            now=now, grace_ms=grace_ms,
        )
        return sum(1 for bar in bars if self.save_bar(bar))

    def run_startup_backlog(self, *, now: datetime, grace_ms: int) -> int:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT max(bar_time) FROM minute_ma_integrated_realtime_minute_bar
                    WHERE bar_time::date=%s""", (now.date(),),
            )
            last = cursor.fetchone()[0]
        start = (last - timedelta(minutes=1)) if last is not None else now.replace(
            hour=0, minute=0, second=0, microsecond=0)
        bars = build_realtime_minute_bars(
            self.execution_ticks(since=start, until=now + timedelta(seconds=1)),
            now=now, grace_ms=grace_ms,
        )
        return sum(1 for bar in bars if self.save_bar(bar))
