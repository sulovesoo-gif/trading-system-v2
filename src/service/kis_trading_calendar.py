"""KIS 국내휴장일 API 기반 KRX 거래일 목록 제공."""

from __future__ import annotations

from datetime import date, timedelta
from time import sleep as default_sleep
from typing import Callable


class KisTradingCalendar:
    """Calendar API response blocks are chained by their last returned date.

    The official API advises low call frequency. The default interval is one second
    and applies only between calendar API calls, not between minute-bar requests.
    """

    def __init__(self, collector, *, call_interval_seconds: float = 1.0, sleep: Callable[[float], None] = default_sleep) -> None:
        self.collector = collector
        self.call_interval_seconds = call_interval_seconds
        self._sleep = sleep

    def open_dates(self, start_date: date, end_date: date) -> list[date]:
        if end_date < start_date:
            raise ValueError("end_date must not precede start_date.")
        cursor = start_date
        open_dates: list[date] = []
        while cursor <= end_date:
            rows = self.collector.collect(base_date=cursor)
            if not rows:
                raise RuntimeError("KIS 휴장일 API가 빈 목록을 반환했습니다.")
            returned_dates = [row["trade_date"] for row in rows]
            last_date = max(returned_dates)
            if last_date < cursor:
                raise RuntimeError("KIS 휴장일 API 기준일이 진행되지 않았습니다.")
            open_dates.extend(
                row["trade_date"]
                for row in rows
                if start_date <= row["trade_date"] <= end_date and row["open_yn"] == "Y"
            )
            cursor = last_date + timedelta(days=1)
            if cursor <= end_date:
                self._sleep(self.call_interval_seconds)
        return sorted(set(open_dates))
