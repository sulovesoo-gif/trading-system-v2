"""Persistence adapter for H0STCNT0 research minute bars."""

from __future__ import annotations

from datetime import datetime, timedelta

from .realtime_minute import ExecutionTick, RealtimeMinuteBar


class RealtimeMinuteRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def execution_ticks(self, *, since: datetime, until: datetime) -> tuple[ExecutionTick, ...]:
        sql = """SELECT e.stock_code,e.source_event_time,c.connected_at,e.receive_sequence,
                        e.event_index,e.received_at,e.current_price,e.execution_volume,
                        e.accumulated_volume,e.connection_id::text,e.reconnect_flag,
                        e.source_gap_flag,e.event_time_regression_flag,e.duplicate_flag
                   FROM raw_flow_execution e
                   JOIN flow_ws_connection c USING(connection_id)
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
        sql = """INSERT INTO flow_realtime_minute_bar(
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
            bar.ordering_invariant_failure,bar.duplicate_excluded_count, bar.quality_status, list(bar.quality_reasons),
        )
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone() is not None

    def audit_rest(self, bar: RealtimeMinuteBar) -> None:
        """Record later REST evidence without changing the WebSocket bar."""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT open_price,high_price,low_price,close_price,volume,collected_at
              FROM raw_stock_minute WHERE stock_code=%s AND trading_venue='KRX'
               AND data_source='KIS' AND collect_cycle='1MIN' AND bar_time=%s
              ORDER BY collected_at DESC LIMIT 1""", (bar.stock_code,bar.bar_time))
            rest=cursor.fetchone()
        if rest is None:
            values=(None,None,None,None,None,None)
            matches=(None,None,None,None,None)
            status="REST_PENDING";mismatches=[]
        else:
            values=rest
            matches=(bar.open_price==rest[0],bar.high_price==rest[1],bar.low_price==rest[2],
                     bar.close_price==rest[3],bar.volume is not None and bar.volume==rest[4])
            names=("open","high","low","close","volume")
            mismatches=[name for name,matched in zip(names,matches) if not matched]
            status="MATCH" if not mismatches else "MISMATCH"
        sql = """INSERT INTO flow_realtime_minute_rest_audit(
          bar_time,stock_code,ws_open_price,ws_high_price,ws_low_price,ws_close_price,ws_volume,
          ws_finalized_at,rest_open_price,rest_high_price,rest_low_price,rest_close_price,rest_volume,
          rest_collected_at,open_match,high_match,low_match,close_match,volume_match,
          comparison_status,mismatch_fields)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(bar_time,stock_code,trading_venue) DO UPDATE SET
          rest_open_price=EXCLUDED.rest_open_price,rest_high_price=EXCLUDED.rest_high_price,
          rest_low_price=EXCLUDED.rest_low_price,rest_close_price=EXCLUDED.rest_close_price,
          rest_volume=EXCLUDED.rest_volume,rest_collected_at=EXCLUDED.rest_collected_at,
          open_match=EXCLUDED.open_match,high_match=EXCLUDED.high_match,low_match=EXCLUDED.low_match,
          close_match=EXCLUDED.close_match,volume_match=EXCLUDED.volume_match,
          comparison_status=EXCLUDED.comparison_status,mismatch_fields=EXCLUDED.mismatch_fields,
          compared_at=CURRENT_TIMESTAMP"""
        params=(bar.bar_time,bar.stock_code,bar.open_price,bar.high_price,bar.low_price,
                bar.close_price,bar.volume,bar.finalized_at,*values,*matches,status,mismatches)
        with self.pool.connection() as connection, connection.transaction(), connection.cursor() as cursor:
            cursor.execute(sql, params)

    def run_recent(self, *, now: datetime, grace_ms: int) -> tuple[int, int]:
        from .realtime_minute import build_realtime_minute_bars
        start = now.replace(second=0, microsecond=0) - timedelta(minutes=3)
        ticks = self.execution_ticks(since=start, until=now + timedelta(seconds=1))
        bars = build_realtime_minute_bars(ticks, now=now, grace_ms=grace_ms)
        inserted = audited = 0
        for bar in bars:
            if self.save_bar(bar):
                inserted += 1
            self.audit_rest(bar)
            audited += 1
        return inserted, audited

    def run_startup_backlog(self, *, now: datetime, grace_ms: int) -> tuple[int, int]:
        """Replay unpersisted current-day L0 after restart; conflicts are immutable no-ops."""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT max(bar_time) FROM flow_realtime_minute_bar WHERE bar_time::date=%s",(now.date(),))
            last=cursor.fetchone()[0]
        start=(last-timedelta(minutes=1)) if last is not None else now.replace(hour=0,minute=0,second=0,microsecond=0)
        ticks=self.execution_ticks(since=start,until=now+timedelta(seconds=1))
        from .realtime_minute import build_realtime_minute_bars
        bars=build_realtime_minute_bars(ticks,now=now,grace_ms=grace_ms)
        inserted=audited=0
        for bar in bars:
            inserted += 1 if self.save_bar(bar) else 0
            self.audit_rest(bar);audited+=1
        return inserted,audited
