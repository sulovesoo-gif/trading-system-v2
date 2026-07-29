"""승인된 raw_stock_minute만 읽는 SMA 분석용 조회 저장소."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.analysis.feature.sma_feature import MinuteBar


class StockMinuteAnalysisRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def completed_bars(self, *, stock_code: str, before_time: datetime, limit: int = 120, trading_venue: str = "INTEGRATED") -> list[MinuteBar]:
        sql = (
            "SELECT bar_time, open_price, high_price, low_price, close_price "
            "FROM raw_stock_minute WHERE stock_code = %s AND data_source = 'KIS' "
            "AND market_code = 'KOSPI' AND trading_venue = %s AND collect_cycle = '1MIN' "
            "AND bar_time <= %s ORDER BY bar_time DESC LIMIT %s"
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (stock_code, trading_venue, before_time, limit))
                rows = cursor.fetchall()
        return [MinuteBar(row[0], Decimal(row[1]), Decimal(row[2]), Decimal(row[3]), Decimal(row[4])) for row in reversed(rows)]

    def closes_since(self, *, stock_code: str, start_time: datetime, end_time: datetime, trading_venue: str = "INTEGRATED") -> list[Decimal]:
        sql = (
            "SELECT close_price FROM raw_stock_minute WHERE stock_code = %s AND data_source = 'KIS' "
            "AND market_code = 'KOSPI' AND trading_venue = %s AND collect_cycle = '1MIN' "
            "AND bar_time >= %s AND bar_time <= %s ORDER BY bar_time"
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (stock_code, trading_venue, start_time, end_time))
                return [Decimal(row[0]) for row in cursor.fetchall()]

    def nearest_completed_bar(self, *, stock_code: str, before_time: datetime, trading_venue: str = "KRX") -> MinuteBar | None:
        rows = self.completed_bars(stock_code=stock_code, before_time=before_time, limit=1, trading_venue=trading_venue)
        return rows[-1] if rows else None
