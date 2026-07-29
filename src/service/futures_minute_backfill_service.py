"""KOSPI200 선물 월물별 1분봉 백필 서비스."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from src.repository.backfill_repository import BackfillSegment
from src.repository.raw_specs import RawTable


@dataclass(frozen=True)
class FuturesMinuteBackfillTarget:
    futures_code: str
    market_code: str = "KOSPI200_FUTURES"
    market_division_code: str = "F"
    trading_venue: str = "KRX"


class FuturesMinuteBackfillService:
    """계약·거래일 Segment 하나를 페이지 단위로 수집하고 저장한다."""

    PAGE_SIZE = 102

    def __init__(self, *, collector, ingestion_service, backfill_repository, sleep: Callable[[float], None] | None = None) -> None:
        self.collector = collector
        self.ingestion_service = ingestion_service
        self.backfill_repository = backfill_repository
        self._sleep = sleep

    def run_segment(
        self,
        *,
        segment: BackfillSegment,
        target: FuturesMinuteBackfillTarget,
        trade_date: date,
        input_hour: str = "154500",
        max_pages: int = 100,
    ) -> None:
        if segment.trade_date != trade_date:
            raise ValueError("segment.trade_date must match trade_date.")
        if segment.instrument_code != target.futures_code:
            raise ValueError("segment.instrument_code must match target.futures_code.")
        if target.market_division_code != "F":
            raise ValueError("현재 선물 백필 구현은 KOSPI200 선물 시장구분 F만 지원합니다.")

        self.backfill_repository.mark_running(segment.segment_id)
        cursor_date = segment.cursor_date or trade_date.strftime("%Y%m%d")
        cursor_hour = segment.cursor_time or input_hour
        try:
            for _ in range(max_pages):
                rows = self.collector.collect(
                    futures_code=target.futures_code,
                    market_code=target.market_code,
                    input_date=cursor_date,
                    input_hour=cursor_hour,
                    hour_classification_code="60",
                    previous_data_include_yn="Y",
                    fake_tick_include_yn="N",
                    collect_cycle="1MIN",
                )
                if not rows:
                    raise RuntimeError("선물 분봉 API가 빈 목록을 반환했습니다.")
                if any(row["bar_time"].date() != trade_date for row in rows):
                    raise RuntimeError("선물 분봉 응답에 요청 거래일 외 데이터가 포함되었습니다.")

                write_result = self.ingestion_service.store(RawTable.FUTURES_MINUTE, rows)
                times = [row["bar_time"] for row in rows]
                oldest = min(times)
                is_full_page = len(rows) == self.PAGE_SIZE
                next_cursor = oldest - timedelta(minutes=1) if is_full_page else None
                self.backfill_repository.record_page(
                    segment_id=segment.segment_id,
                    continuation_code=None,
                    cursor_date=next_cursor.strftime("%Y%m%d") if next_cursor else None,
                    cursor_time=next_cursor.strftime("%H%M%S") if next_cursor else None,
                    returned_count=len(rows),
                    inserted_count=write_result.inserted_count,
                    duplicate_count=write_result.duplicate_count,
                    minimum_bar_time=min(times),
                    maximum_bar_time=max(times),
                )
                if next_cursor is None or next_cursor.date() != trade_date:
                    self.backfill_repository.mark_completed(segment.segment_id)
                    return
                cursor_date = next_cursor.strftime("%Y%m%d")
                cursor_hour = next_cursor.strftime("%H%M%S")
                if self._sleep is not None:
                    self._sleep(0.1)
            raise RuntimeError("선물 백필 페이지 최대 횟수를 초과했습니다.")
        except Exception as error:
            self.backfill_repository.mark_failed(segment.segment_id, type(error).__name__)
            raise
