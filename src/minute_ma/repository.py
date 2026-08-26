from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Iterable

from .contracts import Axis, MinuteBar, MinuteMaPath
from .engine import SignalEvent, SignalType


class PostgresMinuteMaRepository:
    def __init__(self, pool, *, write_enabled: bool = False) -> None:
        self.pool = pool
        self.write_enabled = write_enabled
        self._execution_cache: dict[tuple[str,date],dict[datetime,MinuteBar]] = {}

    def paths(self, axis: Axis | None = None) -> tuple[MinuteMaPath, ...]:
        where = "AND p.data_axis=%s" if axis else ""
        params = (axis.value,) if axis else ()
        sql = f"""SELECT p.minute_path_id,p.path_key,p.data_axis,s.signal_code,s.execution_code,
                         s.direction,s.entry_fast_ma,s.entry_slow_ma,s.exit_fast_ma,s.exit_slow_ma,s.trend_ma
                    FROM minute_ma_path p JOIN minute_ma_strategy_master s USING(minute_strategy_id)
                   WHERE p.is_enabled='Y' AND s.is_enabled='Y' {where}
                   ORDER BY p.minute_path_id"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return tuple(MinuteMaPath(
            int(r[0]),str(r[1]),Axis(str(r[2])),str(r[3]),str(r[4]),str(r[5]),
            int(r[6]),int(r[7]),int(r[8]),int(r[9]),int(r[10]) if r[10] is not None else None,
        ) for r in rows)

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
