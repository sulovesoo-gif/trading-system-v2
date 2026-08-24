"""Read-only KRX RAW inputs for the Daily MA V0.3 PAPER runtime."""

from __future__ import annotations

from datetime import date, datetime, time

from .contracts import ExecutionBar


class DailyMaRawProvider:
    """Reads only actual KIS/KRX bars in the frozen regular-session window."""

    def __init__(self, pool) -> None:
        self.pool = pool

    def prior_daily_closes(self, stock_code: str, before: date, limit: int) -> list[float]:
        sql = """SELECT close_price
                   FROM raw_stock_daily
                  WHERE stock_code=%s AND trade_date < %s
                    AND data_source='KIS' AND trading_venue='KRX'
                    AND close_price IS NOT NULL AND close_price > 0
                  ORDER BY trade_date DESC LIMIT %s"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, (stock_code, before, limit))
            rows = cursor.fetchall()
        return [float(row[0]) for row in reversed(rows)]

    def source_bar(self, stock_code: str, at: datetime) -> dict[str, object] | None:
        if at.time() != time(15, 18):
            raise ValueError("Daily MA V0.3 source evaluation is fixed at 15:18 KST")
        sql = """SELECT bar_time,open_price,high_price,low_price,close_price,volume
                   FROM raw_stock_minute
                  WHERE stock_code=%s AND bar_time=%s
                    AND data_source='KIS' AND trading_venue='KRX' AND collect_cycle='1MIN'
                    AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'
                  ORDER BY collected_at DESC NULLS LAST LIMIT 1"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, (stock_code, at))
            row = cursor.fetchone()
        if row is None or row[4] is None or float(row[4]) <= 0:
            return None
        return {"bar_time": row[0].isoformat(), "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]), "volume": int(row[5] or 0)}

    def execution_bars(self, stock_code: str, signal_time: datetime) -> tuple[ExecutionBar, ...]:
        sql = """SELECT bar_time,open_price
                   FROM raw_stock_minute
                  WHERE stock_code=%s AND bar_time::date=%s
                    AND data_source='KIS' AND trading_venue='KRX' AND collect_cycle='1MIN'
                    AND bar_time > %s AND bar_time::time BETWEEN TIME '15:19' AND TIME '15:30'
                    AND open_price IS NOT NULL AND open_price > 0
                  ORDER BY bar_time, collected_at DESC NULLS LAST"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, (stock_code, signal_time.date(), signal_time))
            rows = cursor.fetchall()
        # Raw primary-key guarantees one value per venue/code/time; retain an
        # explicit time de-duplication to make a provider invariant visible.
        unique: dict[datetime, ExecutionBar] = {}
        for row in rows:
            unique.setdefault(row[0], ExecutionBar(row[0], float(row[1])))
        return tuple(unique.values())

    def day20_source_bars(self, stock_code: str, after: datetime, before: datetime | None) -> tuple[dict[str, object], ...]:
        sql = """SELECT bar_time,close_price
                   FROM raw_stock_minute
                  WHERE stock_code=%s AND bar_time > %s
                    AND data_source='KIS' AND trading_venue='KRX' AND collect_cycle='1MIN'
                    AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'
                    AND close_price IS NOT NULL AND close_price > 0
                    AND (%s::timestamp IS NULL OR bar_time < %s::timestamp)
                  ORDER BY bar_time"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, (stock_code, after, before, before))
            rows = cursor.fetchall()
        return tuple({"bar_time": row[0], "close": float(row[1])} for row in rows)
