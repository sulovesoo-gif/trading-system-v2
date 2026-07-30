"""완료된 국내주식·ETF 일봉 RAW 백필 서비스.

Collector는 API 호출과 응답 변환만 수행한다. 이 서비스는 조회 기간 분할,
당일 미완료 일봉 제외, RAW 저장 결과 집계만 담당한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Iterator

from src.repository.raw_repository import RawWriteResult
from src.repository.raw_specs import RawTable


@dataclass(frozen=True)
class StockDailyBackfillTarget:
    stock_code: str
    market_code: str
    trading_venue: str
    start_date: date


@dataclass(frozen=True)
class StockDailyBackfillResult:
    target: StockDailyBackfillTarget
    requested_count: int
    inserted_count: int
    duplicate_count: int
    minimum_trade_date: date | None
    maximum_trade_date: date | None


def split_date_ranges(start_date: date, end_date: date, *, max_days: int = 90) -> Iterator[tuple[date, date]]:
    """Split a long API date range without assuming undocumented response limits."""
    if max_days < 1:
        raise ValueError("max_days must be positive.")
    cursor = start_date
    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=max_days - 1), end_date)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


class StockDailyBackfillService:
    def __init__(self, *, collector, ingestion_service, sleep: Callable[[float], None] | None = None) -> None:
        self.collector = collector
        self.ingestion_service = ingestion_service
        self._sleep = sleep

    def run_target(
        self,
        *,
        target: StockDailyBackfillTarget,
        end_date: date,
        max_days_per_request: int = 90,
        request_interval_seconds: float = 0.0,
    ) -> StockDailyBackfillResult:
        if end_date < target.start_date:
            return StockDailyBackfillResult(target, 0, 0, 0, None, None)

        requested_count = inserted_count = duplicate_count = 0
        dates: list[date] = []
        ranges = list(split_date_ranges(target.start_date, end_date, max_days=max_days_per_request))
        for index, (range_start, range_end) in enumerate(ranges):
            rows = self.collector.collect(
                stock_code=target.stock_code,
                market_code=target.market_code,
                trading_venue=target.trading_venue,
                start_date=range_start.strftime("%Y%m%d"),
                end_date=range_end.strftime("%Y%m%d"),
            )
            # API가 요청 범위를 벗어난 행을 반환해도 완료된 요청 범위만 RAW에 저장한다.
            completed_rows = [
                row for row in rows
                if target.start_date <= row["trade_date"] <= end_date
            ]
            write_result: RawWriteResult = self.ingestion_service.store(
                RawTable.STOCK_DAILY, completed_rows
            )
            requested_count += write_result.requested_count
            inserted_count += write_result.inserted_count
            duplicate_count += write_result.duplicate_count
            dates.extend(row["trade_date"] for row in completed_rows)
            if self._sleep is not None and request_interval_seconds > 0 and index + 1 < len(ranges):
                self._sleep(request_interval_seconds)

        return StockDailyBackfillResult(
            target=target,
            requested_count=requested_count,
            inserted_count=inserted_count,
            duplicate_count=duplicate_count,
            minimum_trade_date=min(dates) if dates else None,
            maximum_trade_date=max(dates) if dates else None,
        )
