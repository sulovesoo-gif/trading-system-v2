from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Iterable

from .contracts import Axis, MinuteBar, MinuteMaPath
from .engine import SignalEvent, SignalType
from .v1_policy import policy_for_direction, stop_event_key
from .v1_live_runtime import V1LiveOpenTrade


@dataclass(frozen=True)
class V1OpenTrade:
    minute_policy_paper_trade_id: int
    entry_execution_time: datetime
    underlying_entry_reference_price: Decimal


@dataclass(frozen=True)
class V1PendingEntry:
    pending_entry_id: int
    minute_policy_path_id: int
    event: SignalEvent
    proxy_bar_time: datetime


class PostgresMinuteMaRepository:
    def __init__(self, pool, *, write_enabled: bool = False) -> None:
        self.pool = pool
        self.write_enabled = write_enabled
        self._execution_cache: dict[tuple[str,date],dict[datetime,MinuteBar]] = {}

    def paths(self, axis: Axis | None = None) -> tuple[MinuteMaPath, ...]:
        where = "AND p.data_axis=%s" if axis else ""
        params = (axis.value,) if axis else ()
        sql = f"""SELECT p.minute_path_id,p.path_key,p.data_axis,s.signal_code,s.execution_code,
                         s.direction,s.entry_fast_ma,s.entry_slow_ma,s.exit_fast_ma,s.exit_slow_ma,
                         s.trend_ma,s.source_daily_strategy_id
                    FROM minute_ma_path p JOIN minute_ma_strategy_master s USING(minute_strategy_id)
                   WHERE p.is_enabled='Y' AND s.is_enabled='Y' {where}
                   ORDER BY p.minute_path_id"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return tuple(MinuteMaPath(
            int(r[0]),str(r[1]),Axis(str(r[2])),str(r[3]),str(r[4]),str(r[5]),
            int(r[6]),int(r[7]),int(r[8]),int(r[9]),int(r[10]) if r[10] is not None else None,
            str(r[11]),
        ) for r in rows)

    def live_paths(self, axis: Axis) -> tuple[MinuteMaPath, ...]:
        sql="""SELECT p.minute_path_id,p.path_key,p.data_axis,s.signal_code,s.execution_code,
          s.direction,s.entry_fast_ma,s.entry_slow_ma,s.exit_fast_ma,s.exit_slow_ma,s.trend_ma,
          s.source_daily_strategy_id FROM minute_ma_path p JOIN minute_ma_strategy_master s USING(minute_strategy_id)
          JOIN minute_ma_operation o USING(minute_path_id) WHERE p.is_enabled='Y' AND s.is_enabled='Y'
          AND p.data_axis=%s AND o.effective_to IS NULL AND o.operation_status='LIVE' ORDER BY p.minute_path_id"""
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute(sql,(axis.value,));rows=cursor.fetchall()
        return tuple(MinuteMaPath(int(r[0]),str(r[1]),Axis(str(r[2])),str(r[3]),str(r[4]),str(r[5]),
          int(r[6]),int(r[7]),int(r[8]),int(r[9]),int(r[10]) if r[10] is not None else None,str(r[11])) for r in rows)

    def v1_policy_paths(self, *, live_only: bool = False) -> tuple[MinuteMaPath, ...]:
        sql = """SELECT p.minute_path_id,pp.policy_path_key,p.data_axis,s.signal_code,s.execution_code,
                 s.direction,s.entry_fast_ma,s.entry_slow_ma,s.exit_fast_ma,s.exit_slow_ma,s.trend_ma,
                 s.source_daily_strategy_id,pp.minute_policy_path_id
            FROM minute_ma_policy_path pp
            JOIN minute_ma_path p USING(minute_path_id)
            JOIN minute_ma_strategy_master s USING(minute_strategy_id)
            LEFT JOIN minute_ma_policy_operation po
              ON po.minute_policy_path_id=pp.minute_policy_path_id AND po.effective_to IS NULL
           WHERE pp.is_enabled='Y' AND p.is_enabled='Y' AND s.is_enabled='Y'
             AND (%s=FALSE OR po.operation_status='LIVE')
           ORDER BY pp.minute_policy_path_id"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql,(live_only,)); rows = cursor.fetchall()
        return tuple(MinuteMaPath(
            int(r[0]), str(r[1]), Axis(str(r[2])), str(r[3]), str(r[4]), str(r[5]),
            int(r[6]), int(r[7]), int(r[8]), int(r[9]),
            int(r[10]) if r[10] is not None else None, str(r[11]), int(r[12]),
            policy_for_direction(str(r[5])),
        ) for r in rows)

    def v1_runtime_cursor(self, *, signal_code: str) -> datetime | None:
        if not self.write_enabled:
            return None
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT last_source_bar_time FROM minute_ma_policy_runtime_cursor
               WHERE runtime_name='MINUTE_MA_V1_PAPER' AND policy_version='V1.0' AND signal_code=%s""",
                           (signal_code,)); row = cursor.fetchone()
        return None if row is None else row[0]

    def advance_v1_cursor(self, *, signal_code: str, last_source_bar_time: datetime) -> None:
        if not self.write_enabled:
            return
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minute_ma_policy_runtime_cursor(
                 runtime_name,policy_version,signal_code,last_source_bar_time)
               VALUES('MINUTE_MA_V1_PAPER','V1.0',%s,%s)
               ON CONFLICT(runtime_name,policy_version,signal_code) DO UPDATE
               SET last_source_bar_time=GREATEST(minute_ma_policy_runtime_cursor.last_source_bar_time,
                                                  EXCLUDED.last_source_bar_time),
                   updated_at=CURRENT_TIMESTAMP""", (signal_code,last_source_bar_time))
            connection.commit()

    def v1_defer_entry(self, *, path: MinuteMaPath, event: SignalEvent,
                       proxy_bar_time: datetime, pending_reason: str) -> int:
        """Durably preserve an eligible signal before advancing the source cursor."""
        if not self.write_enabled:
            return 0
        import json
        snapshot = json.dumps({"ma":event.ma_values,"previous_ma":event.previous_ma_values,
                               "trend_passed":event.trend_passed}, sort_keys=True)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minute_ma_policy_paper_pending_entry(
              minute_policy_path_id,signal_event_key,source_bar_time,confirmed_at,proxy_bar_time,
              source_snapshot,pending_reason,signal_source,source_bar_finalized_at,evaluated_at)
              VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,CURRENT_TIMESTAMP)
              ON CONFLICT(minute_policy_path_id,signal_event_key) DO UPDATE SET
                last_checked_at=CURRENT_TIMESTAMP,pending_reason=EXCLUDED.pending_reason
              RETURNING pending_entry_id""",
              (path.minute_policy_path_id,event.signal_event_key,event.source_bar_time,
               event.confirmed_at,proxy_bar_time,snapshot,pending_reason,event.signal_source,
               event.confirmed_at if event.signal_source=='KIS_H0STCNT0_REALTIME' else None))
            pending_id=int(cursor.fetchone()[0]);connection.commit();return pending_id

    def v1_pending_entries(self, *, policy_path_ids: tuple[int, ...]) -> tuple[V1PendingEntry, ...]:
        if not self.write_enabled or not policy_path_ids:
            return ()
        import json
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT pending_entry_id,minute_policy_path_id,signal_event_key,
              source_bar_time,confirmed_at,proxy_bar_time,source_snapshot
              FROM minute_ma_policy_paper_pending_entry
              WHERE pending_status='PENDING' AND minute_policy_path_id=ANY(%s)
              ORDER BY proxy_bar_time,pending_entry_id""", (list(policy_path_ids),))
            rows=cursor.fetchall()
        pending=[]
        for row in rows:
            snapshot=row[6] if isinstance(row[6],dict) else json.loads(row[6])
            event=SignalEvent(0,"V1_PENDING",SignalType.ENTRY,row[3],row[4],str(row[2]),
                              bool(snapshot.get("trend_passed",True)),snapshot.get("ma",{}),
                              snapshot.get("previous_ma",{}),"KIS_H0STCNT0_REALTIME")
            pending.append(V1PendingEntry(int(row[0]),int(row[1]),event,row[5]))
        return tuple(pending)

    def snapshot_v1_telemetry(self, *, snapshot_date: date) -> int:
        """Persist one immutable daily rank snapshot; it never changes Operation."""
        if not self.write_enabled:return 0
        sql="""INSERT INTO minute_ma_v1_daily_telemetry_snapshot(
          snapshot_date,minute_policy_path_id,recent_5_compound_pct,rank_no,top20_consecutive_days)
        SELECT %s,d.minute_policy_path_id,d.recent_5_compound_pct,d.current_rank,
          CASE WHEN d.current_rank<=20 THEN COALESCE(p.top20_consecutive_days,0)+1 ELSE 0 END
        FROM vw_minute_ma_v1_policy_dashboard d
        LEFT JOIN LATERAL(
          SELECT x.top20_consecutive_days FROM minute_ma_v1_daily_telemetry_snapshot x
          WHERE x.minute_policy_path_id=d.minute_policy_path_id AND x.snapshot_date<%s
          ORDER BY x.snapshot_date DESC LIMIT 1
        ) p ON TRUE
        ON CONFLICT(snapshot_date,minute_policy_path_id) DO NOTHING"""
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute(sql,(snapshot_date,snapshot_date));inserted=cursor.rowcount;connection.commit()
        return inserted

    def v1_live_runtime_cursor(self, *, signal_code: str) -> datetime | None:
        if not self.write_enabled:return None
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("""SELECT last_source_bar_time FROM minute_ma_policy_runtime_cursor
              WHERE runtime_name='MINUTE_MA_V1_LIVE_NOSEND' AND policy_version='V1.0' AND signal_code=%s""",
              (signal_code,));row=cursor.fetchone()
        return None if row is None else row[0]

    def advance_v1_live_cursor(self, *, signal_code: str, last_source_bar_time: datetime) -> None:
        if not self.write_enabled:return
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minute_ma_policy_runtime_cursor(
              runtime_name,policy_version,signal_code,last_source_bar_time)
              VALUES('MINUTE_MA_V1_LIVE_NOSEND','V1.0',%s,%s)
              ON CONFLICT(runtime_name,policy_version,signal_code) DO UPDATE SET
              last_source_bar_time=GREATEST(minute_ma_policy_runtime_cursor.last_source_bar_time,
                                             EXCLUDED.last_source_bar_time),updated_at=CURRENT_TIMESTAMP""",
              (signal_code,last_source_bar_time));connection.commit()

    def live_runtime_cursor(self,*,axis:Axis,signal_code:str):
        with self.pool.connection() as c,c.cursor() as q:
            q.execute("SELECT last_source_bar_time FROM minute_ma_runtime_cursor WHERE runtime_name='MINUTE_MA_LIVE_V01' AND data_axis=%s AND signal_code=%s",(axis.value,signal_code));row=q.fetchone()
        return None if row is None else row[0]

    def advance_live_cursor(self,*,axis:Axis,signal_code:str,last_source_bar_time:datetime):
        with self.pool.connection() as c,c.cursor() as q:
            q.execute("""INSERT INTO minute_ma_runtime_cursor(runtime_name,data_axis,signal_code,last_source_bar_time)
              VALUES('MINUTE_MA_LIVE_V01',%s,%s,%s) ON CONFLICT(runtime_name,data_axis,signal_code) DO UPDATE
              SET last_source_bar_time=GREATEST(minute_ma_runtime_cursor.last_source_bar_time,EXCLUDED.last_source_bar_time),updated_at=CURRENT_TIMESTAMP""",
              (axis.value,signal_code,last_source_bar_time));c.commit()

    def source_bars(self, *, stock_code: str, axis: Axis, trading_date: date) -> tuple[MinuteBar, ...]:
        start, end = axis.session
        day_start=datetime.combine(trading_date,time.min)
        day_end=day_start+timedelta(days=1)
        if axis.continuity.value == "CONTINUOUS":
            sql = """WITH prior AS (
                       SELECT bar_time,open_price,high_price,low_price,close_price,volume
                         FROM raw_stock_minute
                        WHERE stock_code=%s AND data_source='KIS' AND trading_venue=%s
                          AND collect_cycle='1MIN' AND bar_time<%s
                          AND bar_time::time BETWEEN %s AND %s
                        ORDER BY bar_time DESC LIMIT 50
                     ), current_day AS (
                       SELECT bar_time,open_price,high_price,low_price,close_price,volume
                         FROM raw_stock_minute
                        WHERE stock_code=%s AND data_source='KIS' AND trading_venue=%s
                          AND collect_cycle='1MIN' AND bar_time>=%s AND bar_time<%s
                          AND bar_time::time BETWEEN %s AND %s
                     ) SELECT * FROM prior UNION ALL SELECT * FROM current_day ORDER BY bar_time"""
            params = (stock_code,axis.market_source.value,day_start,start,end,
                      stock_code,axis.market_source.value,day_start,day_end,start,end)
        else:
            sql = """SELECT bar_time,open_price,high_price,low_price,close_price,volume
                       FROM raw_stock_minute
                      WHERE stock_code=%s AND data_source='KIS' AND trading_venue=%s
                        AND collect_cycle='1MIN' AND bar_time>=%s AND bar_time<%s
                        AND bar_time::time BETWEEN %s AND %s ORDER BY bar_time"""
            params = (stock_code,axis.market_source.value,day_start,day_end,start,end)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return tuple(MinuteBar(r[0],float(r[1]),float(r[2]),float(r[3]),float(r[4]),int(r[5] or 0)) for r in rows)

    def v1_source_bars(self, *, stock_code: str, trading_date: date) -> tuple[MinuteBar, ...]:
        """Realtime-authoritative V1 stream with pre-cutover REST history only."""
        day_start=datetime.combine(trading_date,time.min);day_end=day_start+timedelta(days=1)
        sql="""WITH cutover AS (
          SELECT min(bar_time) AS at FROM flow_realtime_minute_bar WHERE stock_code=%s
        ), rest_dedup AS (
          SELECT DISTINCT ON (r.bar_time) r.bar_time,r.open_price,r.high_price,r.low_price,r.close_price,r.volume
          FROM raw_stock_minute r CROSS JOIN cutover c
          WHERE c.at IS NOT NULL AND r.stock_code=%s AND r.data_source='KIS'
            AND r.trading_venue='KRX' AND r.collect_cycle='1MIN' AND r.bar_time<c.at
            AND r.bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'
          ORDER BY r.bar_time,r.collected_at DESC
        ), all_bars AS (
          SELECT r.bar_time,r.open_price,r.high_price,r.low_price,r.close_price,r.volume,
                 NULL::timestamp AS finalized_at,TRUE AS signal_eligible,'REST_1MIN_PRE_CUTOVER'::text AS source_name
          FROM rest_dedup r
          UNION ALL
          SELECT w.bar_time,w.open_price,w.high_price,w.low_price,w.close_price,w.volume,
                 w.finalized_at,
                 (w.quality_status<>'INCOMPLETE' AND NOT w.source_gap_flag AND NOT w.reconnect_flag
                  AND NOT w.event_time_regression_flag AND NOT w.ordering_invariant_failure
                  AND NOT w.accumulated_volume_regression),
                 'KIS_H0STCNT0_REALTIME'::text
          FROM flow_realtime_minute_bar w WHERE w.stock_code=%s
        ), prior AS (
          SELECT * FROM all_bars WHERE bar_time<%s ORDER BY bar_time DESC LIMIT 50
        ), current_day AS (
          SELECT * FROM all_bars WHERE bar_time>=%s AND bar_time<%s
        ) SELECT * FROM prior UNION ALL SELECT * FROM current_day ORDER BY bar_time"""
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute(sql,(stock_code,stock_code,stock_code,day_start,day_start,day_end));rows=cursor.fetchall()
        return tuple(MinuteBar(r[0],float(r[1]),float(r[2]),float(r[3]),float(r[4]),int(r[5] or 0),
                               r[6],bool(r[7]),str(r[8])) for r in rows)

    def v1_realtime_bar(self, *, stock_code: str, at: datetime) -> MinuteBar | None:
        """Exact real-trade OPEN proxy; no REST fallback and no synthetic bar."""
        sql="""SELECT bar_time,open_price,high_price,low_price,close_price,volume,finalized_at
          FROM flow_realtime_minute_bar WHERE stock_code=%s AND bar_time=%s
           AND quality_status<>'INCOMPLETE' AND NOT source_gap_flag AND NOT reconnect_flag
           AND NOT event_time_regression_flag AND NOT ordering_invariant_failure
           AND NOT accumulated_volume_regression"""
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute(sql,(stock_code,at));row=cursor.fetchone()
        return None if row is None else MinuteBar(row[0],float(row[1]),float(row[2]),float(row[3]),
          float(row[4]),int(row[5] or 0),row[6],True,"KIS_H0STCNT0_REALTIME")

    def execution_bar(self, *, stock_code: str, at: datetime) -> MinuteBar | None:
        if not time(9,0) <= at.time() <= time(15,19):
            return None
        key=(stock_code,at.date())
        cached=self._execution_cache.get(key)
        if cached is None:
            day_start=datetime.combine(at.date(),time.min);day_end=day_start+timedelta(days=1)
            sql = """SELECT DISTINCT ON (bar_time)
                          bar_time,open_price,high_price,low_price,close_price,volume
                   FROM raw_stock_minute
                  WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                    AND collect_cycle='1MIN' AND bar_time>=%s AND bar_time<%s
                    AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:19'
                  ORDER BY bar_time,collected_at DESC NULLS LAST"""
            with self.pool.connection() as connection, connection.cursor() as cursor:
                cursor.execute(sql, (stock_code,day_start,day_end));rows=cursor.fetchall()
            cached={r[0]:MinuteBar(r[0],float(r[1]),float(r[2]),float(r[3]),float(r[4]),int(r[5] or 0)) for r in rows}
            self._execution_cache[key]=cached
        return cached.get(at)

    def underlying_bar(self, *, stock_code: str, at: datetime) -> MinuteBar | None:
        """Actual KRX underlying bar used by the frozen STOP validation contract."""
        if not time(9,0) <= at.time() <= time(15,30):
            return None
        sql = """SELECT bar_time,open_price,high_price,low_price,close_price,volume
                   FROM raw_stock_minute
                  WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                    AND collect_cycle='1MIN' AND bar_time=%s
                  ORDER BY collected_at DESC NULLS LAST LIMIT 1"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql,(stock_code,at)); row=cursor.fetchone()
        return None if row is None else MinuteBar(
            row[0],float(row[1]),float(row[2]),float(row[3]),float(row[4]),int(row[5] or 0))

    def execution_watermark(self,*,stock_code:str,trading_date:date) -> datetime | None:
        probe=datetime.combine(trading_date,time(9,0))
        self.execution_bar(stock_code=stock_code,at=probe)
        cached=self._execution_cache.get((stock_code,trading_date),{})
        return max(cached) if cached else None

    def runtime_cursor(self,*,axis:Axis,signal_code:str) -> datetime | None:
        if not self.write_enabled:return None
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("""SELECT last_source_bar_time FROM minute_ma_runtime_cursor
                              WHERE runtime_name='MINUTE_MA_PAPER_V01' AND data_axis=%s AND signal_code=%s""",
                           (axis.value,signal_code));row=cursor.fetchone()
        return None if row is None else row[0]

    def advance_cursor(self,*,axis:Axis,signal_code:str,last_source_bar_time:datetime) -> None:
        if not self.write_enabled:return
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minute_ma_runtime_cursor(runtime_name,data_axis,signal_code,last_source_bar_time)
              VALUES ('MINUTE_MA_PAPER_V01',%s,%s,%s)
              ON CONFLICT(runtime_name,data_axis,signal_code) DO UPDATE
              SET last_source_bar_time=GREATEST(minute_ma_runtime_cursor.last_source_bar_time,EXCLUDED.last_source_bar_time),
                  updated_at=CURRENT_TIMESTAMP""",(axis.value,signal_code,last_source_bar_time));connection.commit()

    def record_non_executable(self, event: SignalEvent, *, status: str) -> bool:
        if not self.write_enabled:
            return False
        return self._record_event(event, proxy_bar=None, status=status)

    def _record_event(self, event: SignalEvent, *, proxy_bar: MinuteBar | None, status: str) -> bool:
        sql = """INSERT INTO minute_ma_paper_event(
                   minute_path_id,signal_event_key,event_type,source_bar_time,confirmed_at,
                   proxy_bar_time,proxy_price,event_status,source_snapshot)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                 ON CONFLICT (minute_path_id,signal_event_key,event_type) DO NOTHING
                 RETURNING minute_paper_event_id"""
        import json
        snapshot = json.dumps({"ma":event.ma_values,"previous_ma":event.previous_ma_values,
                               "trend_passed":event.trend_passed},sort_keys=True)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql,(event.minute_path_id,event.signal_event_key,event.signal_type.value,
                               event.source_bar_time,event.confirmed_at,
                               proxy_bar.bar_time if proxy_bar else None,
                               Decimal(str(proxy_bar.open_price)) if proxy_bar else None,status,snapshot))
            created = cursor.fetchone() is not None
            connection.commit()
        return created

    def apply_event(self, *, path: MinuteMaPath, event: SignalEvent, proxy_bar: MinuteBar) -> tuple[int, int]:
        """Persist one event and its trade transitions atomically.

        Returns (created trades, closed trades). Duplicate replay returns (0,0).
        """
        if not self.write_enabled:
            return 0,0
        import json
        snapshot = json.dumps({"ma":event.ma_values,"previous_ma":event.previous_ma_values,
                               "trend_passed":event.trend_passed},sort_keys=True)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minute_ma_paper_event(
                 minute_path_id,signal_event_key,event_type,source_bar_time,confirmed_at,
                 proxy_bar_time,proxy_price,event_status,source_snapshot)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,'CREATED',%s::jsonb)
                 ON CONFLICT (minute_path_id,signal_event_key,event_type) DO NOTHING RETURNING minute_paper_event_id""",
                 (path.minute_path_id,event.signal_event_key,event.signal_type.value,event.source_bar_time,
                  event.confirmed_at,proxy_bar.bar_time,Decimal(str(proxy_bar.open_price)),snapshot))
            if cursor.fetchone() is None:
                connection.rollback()
                return 0,0
            if event.signal_type is SignalType.ENTRY:
                cursor.execute("SELECT current_capital FROM minute_ma_paper_capital WHERE minute_path_id=%s FOR UPDATE",
                               (path.minute_path_id,))
                capital = cursor.fetchone()[0]
                cursor.execute("""INSERT INTO minute_ma_paper_trade(
                  minute_path_id,entry_event_key,trade_status,entry_signal_time,entry_execution_time,
                  entry_price,basis_capital) VALUES (%s,%s,'OPEN',%s,%s,%s,%s)
                  ON CONFLICT (minute_path_id,entry_event_key) DO NOTHING RETURNING minute_paper_trade_id""",
                  (path.minute_path_id,event.signal_event_key,event.confirmed_at,proxy_bar.bar_time,
                   Decimal(str(proxy_bar.open_price)),capital))
                created = 1 if cursor.fetchone() else 0
                connection.commit()
                return created,0
            closed = self._close_open(cursor=cursor,path=path,signal_time=event.confirmed_at,
                                      proxy_bar=proxy_bar,reason="NORMAL_EXIT")
            connection.commit()
            return 0,closed

    @staticmethod
    def _close_open(*, cursor, path: MinuteMaPath, signal_time: datetime,
                    proxy_bar: MinuteBar, reason: str) -> int:
        cursor.execute("""SELECT minute_paper_trade_id,entry_price,basis_capital
                            FROM minute_ma_paper_trade
                           WHERE minute_path_id=%s AND trade_status='OPEN'
                             AND entry_execution_time<=%s
                           ORDER BY entry_execution_time,minute_paper_trade_id FOR UPDATE""",
                       (path.minute_path_id,proxy_bar.bar_time))
        rows = cursor.fetchall()
        for trade_id,entry_price,basis_capital in rows:
            gross = (Decimal(str(proxy_bar.open_price))/entry_price-Decimal("1"))*Decimal("100")
            net = gross-Decimal("0.20")
            pnl = basis_capital*net/Decimal("100")
            cursor.execute("""UPDATE minute_ma_paper_trade SET trade_status='CLOSED',exit_signal_time=%s,
                   exit_execution_time=%s,exit_price=%s,exit_reason=%s,gross_return_pct=%s,
                   net_return_pct=%s,realized_pnl=%s,updated_at=CURRENT_TIMESTAMP
                 WHERE minute_paper_trade_id=%s AND trade_status='OPEN'""",
                 (signal_time,proxy_bar.bar_time,Decimal(str(proxy_bar.open_price)),reason,gross,net,pnl,trade_id))
            cursor.execute("""INSERT INTO minute_ma_paper_settlement(
                   minute_paper_trade_id,minute_path_id,realized_pnl,capital_after)
                 SELECT %s,%s,%s,current_capital+%s FROM minute_ma_paper_capital
                  WHERE minute_path_id=%s ON CONFLICT DO NOTHING RETURNING capital_after""",
                 (trade_id,path.minute_path_id,pnl,pnl,path.minute_path_id))
            settled = cursor.fetchone()
            if settled is not None:
                cursor.execute("""UPDATE minute_ma_paper_capital
                                     SET current_capital=current_capital+%s,
                                         cumulative_realized_pnl=cumulative_realized_pnl+%s,
                                         version=version+1,updated_at=CURRENT_TIMESTAMP
                                   WHERE minute_path_id=%s""",(pnl,pnl,path.minute_path_id))
        return len(rows)

    def close_eod(self, *, path: MinuteMaPath, trading_date: date) -> int:
        if not self.write_enabled:
            return 0
        proxy_time = datetime.combine(trading_date,time(15,19))
        bar = self.execution_bar(stock_code=path.execution_code,at=proxy_time)
        if bar is None:
            return 0
        from hashlib import sha256
        import json
        signal_time = proxy_time+timedelta(minutes=1,seconds=1)
        event_key = sha256(
            f"MINUTE_MA_V01|{path.path_key}|EOD_EXIT|{proxy_time.isoformat()}".encode("utf-8")
        ).hexdigest()
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minute_ma_paper_event(
                  minute_path_id,signal_event_key,event_type,source_bar_time,confirmed_at,
                  proxy_bar_time,proxy_price,event_status,source_snapshot)
                VALUES (%s,%s,'EOD_EXIT',%s,%s,%s,%s,'CREATED',%s::jsonb)
                ON CONFLICT (minute_path_id,signal_event_key,event_type) DO NOTHING
                RETURNING minute_paper_event_id""",
                (path.minute_path_id,event_key,proxy_time,signal_time,proxy_time,
                 Decimal(str(bar.open_price)),json.dumps({"contract":"EOD_1519_OPEN"})))
            if cursor.fetchone() is None:
                connection.rollback()
                return 0
            closed = self._close_open(cursor=cursor,path=path,
                                      signal_time=signal_time,
                                      proxy_bar=bar,reason="EOD_1519")
            connection.commit()
        return closed

    def v1_open_trade(self, *, path: MinuteMaPath, event: SignalEvent,
                      execution_bar: MinuteBar,
                      underlying_entry_reference_price: Decimal,
                      pending_entry_id: int | None = None) -> int:
        if not self.write_enabled:
            return 0
        policy = path.operation_policy
        threshold = policy.threshold(underlying_entry_reference_price)
        import json
        snapshot=json.dumps({"ma":event.ma_values,"previous_ma":event.previous_ma_values,
                             "trend_passed":event.trend_passed,"policy":policy.policy_code},sort_keys=True)
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minute_ma_policy_paper_event(
              minute_policy_path_id,signal_event_key,event_type,source_bar_time,confirmed_at,
              proxy_bar_time,proxy_price,underlying_price,source_snapshot,signal_source,
              source_bar_finalized_at,evaluated_at)
              VALUES(%s,%s,'ENTRY',%s,%s,%s,%s,%s,%s::jsonb,%s,%s,CURRENT_TIMESTAMP)
              ON CONFLICT(minute_policy_path_id,signal_event_key,event_type) DO NOTHING RETURNING 1""",
              (path.minute_policy_path_id,event.signal_event_key,event.source_bar_time,event.confirmed_at,
               execution_bar.bar_time,Decimal(str(execution_bar.open_price)),
               underlying_entry_reference_price,snapshot,event.signal_source,
               event.confirmed_at if event.signal_source=='KIS_H0STCNT0_REALTIME' else None))
            if cursor.fetchone() is None:
                if pending_entry_id is not None:
                    cursor.execute("""UPDATE minute_ma_policy_paper_pending_entry
                      SET pending_status='COMPLETED',resolved_at=CURRENT_TIMESTAMP,last_checked_at=CURRENT_TIMESTAMP
                      WHERE pending_entry_id=%s AND pending_status='PENDING'""",(pending_entry_id,))
                    connection.commit()
                else:
                    connection.rollback()
                return 0
            cursor.execute("SELECT current_capital FROM minute_ma_policy_paper_capital WHERE minute_policy_path_id=%s FOR UPDATE",
                           (path.minute_policy_path_id,)); capital=cursor.fetchone()[0]
            cursor.execute("""INSERT INTO minute_ma_policy_paper_trade(
              minute_policy_path_id,entry_event_key,trade_status,entry_signal_time,entry_execution_time,
              entry_price,underlying_entry_reference_price,stop_threshold_price,stop_policy,basis_capital)
              VALUES(%s,%s,'OPEN',%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(minute_policy_path_id,entry_event_key) DO NOTHING RETURNING 1""",
              (path.minute_policy_path_id,event.signal_event_key,event.confirmed_at,execution_bar.bar_time,
               Decimal(str(execution_bar.open_price)),underlying_entry_reference_price,threshold,
               "UNDERLYING_1PCT" if policy.direction=="SHORT" else "UNDERLYING_5PCT",capital))
            created=1 if cursor.fetchone() is not None else 0
            if pending_entry_id is not None:
                cursor.execute("""UPDATE minute_ma_policy_paper_pending_entry
                  SET pending_status='COMPLETED',resolved_at=CURRENT_TIMESTAMP,last_checked_at=CURRENT_TIMESTAMP
                  WHERE pending_entry_id=%s AND pending_status='PENDING'""",(pending_entry_id,))
            connection.commit(); return created

    def v1_open_trades(self, *, path: MinuteMaPath) -> tuple[V1OpenTrade,...]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT minute_policy_paper_trade_id,entry_execution_time,
                    underlying_entry_reference_price
               FROM minute_ma_policy_paper_trade
              WHERE minute_policy_path_id=%s AND trade_status='OPEN'
              ORDER BY entry_execution_time,minute_policy_paper_trade_id""",
              (path.minute_policy_path_id,)); rows=cursor.fetchall()
        return tuple(V1OpenTrade(int(r[0]),r[1],Decimal(r[2])) for r in rows)

    def v1_live_open_trades(self, *, path: MinuteMaPath) -> tuple[V1LiveOpenTrade,...]:
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT t.minute_live_trade_id,t.minute_policy_path_id,t.ownership_id,
                 t.underlying_entry_reference_price,COALESCE(min(a.broker_event_time),t.created_at)
              FROM minute_ma_live_trade t LEFT JOIN minute_ma_live_checkpoint_allocation a
                ON a.minute_live_trade_id=t.minute_live_trade_id AND a.side='BUY'
             WHERE t.minute_policy_path_id=%s AND t.trade_status='OPEN'
               AND t.underlying_entry_reference_price IS NOT NULL
             GROUP BY t.minute_live_trade_id,t.minute_policy_path_id,t.ownership_id,
                      t.underlying_entry_reference_price,t.created_at
             ORDER BY t.minute_live_trade_id""",(path.minute_policy_path_id,));rows=cursor.fetchall()
        return tuple(V1LiveOpenTrade(int(r[0]),int(r[1]),str(r[2]),Decimal(r[3]),r[4]) for r in rows)

    def v1_close_normal(self, *, path: MinuteMaPath, event: SignalEvent,
                        execution_bar: MinuteBar) -> int:
        if not self.write_enabled:
            return 0
        import json
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minute_ma_policy_paper_event(
              minute_policy_path_id,signal_event_key,event_type,source_bar_time,confirmed_at,
              proxy_bar_time,proxy_price,source_snapshot,signal_source,source_bar_finalized_at,evaluated_at)
              VALUES(%s,%s,'NORMAL_EXIT',%s,%s,%s,%s,%s::jsonb,%s,%s,CURRENT_TIMESTAMP)
              ON CONFLICT(minute_policy_path_id,signal_event_key,event_type) DO NOTHING RETURNING 1""",
              (path.minute_policy_path_id,event.signal_event_key,event.source_bar_time,event.confirmed_at,
               execution_bar.bar_time,Decimal(str(execution_bar.open_price)),
               json.dumps({"ma":event.ma_values,"previous_ma":event.previous_ma_values},sort_keys=True),
               event.signal_source,event.confirmed_at if event.signal_source=='KIS_H0STCNT0_REALTIME' else None))
            if cursor.fetchone() is None:
                connection.rollback(); return 0
            cursor.execute("""SELECT minute_policy_paper_trade_id FROM minute_ma_policy_paper_trade
               WHERE minute_policy_path_id=%s AND trade_status='OPEN' AND entry_execution_time<=%s
               ORDER BY entry_execution_time,minute_policy_paper_trade_id FOR UPDATE""",
               (path.minute_policy_path_id,execution_bar.bar_time)); ids=[int(r[0]) for r in cursor.fetchall()]
            for trade_id in ids:
                self._settle_v1(cursor=cursor,trade_id=trade_id,signal_time=event.confirmed_at,
                                execution_bar=execution_bar,reason="NORMAL_EXIT")
            connection.commit(); return len(ids)

    def v1_close_stop(self, *, path: MinuteMaPath, trade: V1OpenTrade,
                      trigger_bar_time: datetime, trigger_underlying_close: Decimal,
                      execution_bar: MinuteBar,
                      trigger_confirmed_at: datetime | None = None) -> int:
        if not self.write_enabled:
            return 0
        key=stop_event_key(policy_path_id=int(path.minute_policy_path_id),
                           trade_id=trade.minute_policy_paper_trade_id,
                           trigger_bar_time=trigger_bar_time)
        confirmed=trigger_confirmed_at or trigger_bar_time+timedelta(minutes=1,seconds=1)
        import json
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minute_ma_policy_paper_event(
              minute_policy_path_id,signal_event_key,event_type,source_bar_time,confirmed_at,
              proxy_bar_time,proxy_price,underlying_price,source_snapshot,signal_source,
              source_bar_finalized_at,evaluated_at)
              VALUES(%s,%s,'STOP_EXIT',%s,%s,%s,%s,%s,%s::jsonb,'KIS_H0STCNT0_REALTIME',%s,CURRENT_TIMESTAMP)
              ON CONFLICT(minute_policy_path_id,signal_event_key,event_type) DO NOTHING RETURNING 1""",
              (path.minute_policy_path_id,key,trigger_bar_time,confirmed,execution_bar.bar_time,
               Decimal(str(execution_bar.open_price)),trigger_underlying_close,
               json.dumps({"target_trade_id":trade.minute_policy_paper_trade_id,
                           "anchor":str(trade.underlying_entry_reference_price)},sort_keys=True),confirmed))
            if cursor.fetchone() is None:
                connection.rollback(); return 0
            cursor.execute("""SELECT 1 FROM minute_ma_policy_paper_trade
              WHERE minute_policy_paper_trade_id=%s AND trade_status='OPEN' FOR UPDATE""",
              (trade.minute_policy_paper_trade_id,))
            if cursor.fetchone() is None:
                connection.rollback(); return 0
            self._settle_v1(cursor=cursor,trade_id=trade.minute_policy_paper_trade_id,
                            signal_time=confirmed,execution_bar=execution_bar,reason="STOP_EXIT",
                            stop_trigger_time=trigger_bar_time,
                            stop_trigger_underlying_close=trigger_underlying_close)
            connection.commit(); return 1

    @staticmethod
    def _settle_v1(*, cursor, trade_id: int, signal_time: datetime,
                   execution_bar: MinuteBar, reason: str,
                   stop_trigger_time: datetime | None = None,
                   stop_trigger_underlying_close: Decimal | None = None) -> None:
        cursor.execute("""SELECT minute_policy_path_id,entry_price,basis_capital
          FROM minute_ma_policy_paper_trade WHERE minute_policy_paper_trade_id=%s""",(trade_id,))
        path_id,entry_price,basis=cursor.fetchone()
        price=Decimal(str(execution_bar.open_price))
        gross=(price/Decimal(entry_price)-Decimal("1"))*Decimal("100")
        net=gross-Decimal("0.20");pnl=Decimal(basis)*net/Decimal("100")
        cursor.execute("""UPDATE minute_ma_policy_paper_trade SET trade_status='CLOSED',
          exit_signal_time=%s,exit_execution_time=%s,exit_price=%s,exit_reason=%s,
          stop_trigger_time=%s,stop_trigger_underlying_close=%s,gross_return_pct=%s,
          net_return_pct=%s,realized_pnl=%s,updated_at=CURRENT_TIMESTAMP
          WHERE minute_policy_paper_trade_id=%s AND trade_status='OPEN'""",
          (signal_time,execution_bar.bar_time,price,reason,stop_trigger_time,
           stop_trigger_underlying_close,gross,net,pnl,trade_id))
        cursor.execute("""INSERT INTO minute_ma_policy_paper_settlement(
          minute_policy_paper_trade_id,minute_policy_path_id,realized_pnl,capital_after)
          SELECT %s,%s,%s,current_capital+%s FROM minute_ma_policy_paper_capital
          WHERE minute_policy_path_id=%s ON CONFLICT DO NOTHING RETURNING 1""",
          (trade_id,path_id,pnl,pnl,path_id))
        if cursor.fetchone() is not None:
            cursor.execute("""UPDATE minute_ma_policy_paper_capital
              SET current_capital=current_capital+%s,cumulative_realized_pnl=cumulative_realized_pnl+%s,
                  version=version+1,updated_at=CURRENT_TIMESTAMP WHERE minute_policy_path_id=%s""",
              (pnl,pnl,path_id))
