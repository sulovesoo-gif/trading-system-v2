"""Completed-RAW provider used by Forward through the public Strategy Core API."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.strategy_core.bars import CompletedBar


class PostgresCompletedMinuteProvider:
    """Reads canonical completed KRX one-minute RAW; never collects or writes."""

    def __init__(self, pool, *, venue: str = "KRX") -> None:
        self.pool, self.venue = pool, venue

    def bars(self, stock_code: str, trading_date: str) -> tuple[CompletedBar, ...]:
        sql = """SELECT bar_time,open_price,high_price,low_price,close_price,volume,accumulated_amount
                   FROM raw_stock_minute WHERE stock_code=%s AND data_source='KIS'
                    AND trading_venue=%s AND collect_cycle='1MIN'
                    AND bar_time::date=%s ORDER BY bar_time"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, (stock_code, self.venue, trading_date))
            rows = cursor.fetchall()
        return tuple(CompletedBar(row[0], float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]), float(row[6]) if row[6] is not None else None) for row in rows)

    def bar_at(self, stock_code: str, at: datetime) -> CompletedBar | None:
        return next((bar for bar in self.bars(stock_code, at.date().isoformat()) if bar.time == at), None)

    def next_bar_after(self, stock_code: str, at: datetime) -> CompletedBar | None:
        return next((bar for bar in self.bars(stock_code, at.date().isoformat()) if bar.time > at), None)
