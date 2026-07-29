"""주식·ETF KRX 1분봉 백필 서비스.

Collector는 단일 API 호출과 RAW 행 변환만 수행한다. 이 서비스는 호출 결과의
저장, 페이지 상태 및 중단 재개 메타데이터를 조정한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from src.repository.backfill_repository import BackfillSegment
from src.repository.raw_specs import RawTable


@dataclass(frozen=True)
class StockMinuteBackfillTarget:
    stock_code: str
    market_code: str
    trading_venue: str = "KRX"


class StockMinuteBackfillService:
    """Runs one date-and-product segment at a time; scheduling is intentionally excluded."""

    def __init__(self, *, collector, ingestion_service, backfill_repository, sleep: Callable[[float], None] | None = None) -> None:
        self.collector = collector
        self.ingestion_service = ingestion_service
        self.backfill_repository = backfill_repository
        self._sleep = sleep

    def run_segment(
        self,
        *,
        segment: BackfillSegment,
        target: StockMinuteBackfillTarget,
        trade_date: date,
        input_hour: str = "235959",
        max_pages: int = 100,
    ) -> None:
        """Collect one date segment and persist page progress.

        Actual KRX smoke responses return at most 120 rows without a `tr_cont` header.
        The next request therefore uses one minute before the oldest returned bar as
        the documented date/time cursor. A short page marks the date segment complete.
        """
        if segment.trade_date != trade_date:
            raise ValueError("segment.trade_date must match trade_date.")
        self.backfill_repository.mark_running(segment.segment_id)
        cursor_date = segment.cursor_date or trade_date.strftime("%Y%m%d")
        cursor_hour = segment.cursor_time or input_hour
        try:
            for _ in range(max_pages):
                rows = self.collector.collect(
                    stock_code=target.stock_code,
                    market_code=target.market_code,
                    trading_venue=target.trading_venue,
                    input_date=cursor_date,
                    input_hour=cursor_hour,
                    previous_data_include_yn="N",
                )
                write_result = self.ingestion_service.store(RawTable.STOCK_MINUTE, rows)
                times = [row["bar_time"] for row in rows]
                oldest = min(times) if times else None
                is_full_page = len(rows) == 120
                next_cursor = oldest - timedelta(minutes=1) if oldest and is_full_page else None
                self.backfill_repository.record_page(
                    segment_id=segment.segment_id,
                    continuation_code=None,
                    cursor_date=next_cursor.strftime("%Y%m%d") if next_cursor else None,
                    cursor_time=next_cursor.strftime("%H%M%S") if next_cursor else None,
                    returned_count=len(rows),
                    inserted_count=write_result.inserted_count,
                    duplicate_count=write_result.duplicate_count,
                    minimum_bar_time=min(times) if times else None,
                    maximum_bar_time=max(times) if times else None,
                )
                if next_cursor is None or next_cursor.date() != trade_date:
                    self.backfill_repository.mark_completed(segment.segment_id)
                    return
                cursor_date = next_cursor.strftime("%Y%m%d")
                cursor_hour = next_cursor.strftime("%H%M%S")
                if self._sleep is not None:
                    self._sleep(0.1)
            raise RuntimeError("백필 페이지 최대 횟수를 초과했습니다.")
        except Exception as error:
            self.backfill_repository.mark_failed(segment.segment_id, type(error).__name__)
            raise
