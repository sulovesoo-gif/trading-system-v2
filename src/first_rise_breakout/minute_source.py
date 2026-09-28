"""Read-only KIS same-day completed-minute history for first-rise research."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal
from contextlib import nullcontext

from .models import MinuteBar


class SameDayMinutePeakSource:
    def __init__(self, collector, *, request_lock=None) -> None:
        self.collector = collector
        self.request_lock = request_lock

    def bars_from_open(self, *, stock_code: str, until: datetime) -> list[dict]:
        start = datetime.combine(until.date(), time(9, 0))
        cursor = until
        found: dict[datetime, dict] = {}
        while cursor >= start:
            with self.request_lock or nullcontext():
                rows = self.collector.collect(
                    stock_code=stock_code,
                    market_code="KOSPI",
                    trading_venue="KRX",
                    input_hour=cursor.strftime("%H%M%S"),
                    previous_data_include_yn="Y",
                )
            for row in rows:
                at = row["bar_time"]
                if start <= at <= until:
                    found.setdefault(at, row)
            older = [row["bar_time"] for row in rows if row["bar_time"] < cursor]
            if not older:
                break
            next_cursor = min(older) - timedelta(microseconds=1)
            if next_cursor >= cursor:
                break
            cursor = next_cursor
        return [found[at] for at in sorted(found)]

    def completed_bars_from_open(self, *, stock_code: str, as_of: datetime) -> list[MinuteBar]:
        """Return only bars whose full one-minute interval ended by ``as_of``."""
        completed_before = as_of.replace(second=0, microsecond=0)
        rows = self.bars_from_open(stock_code=stock_code, until=as_of)
        return [
            MinuteBar.from_mapping(row)
            for row in rows
            if row["bar_time"] < completed_before
        ]

    def peak(self, *, stock_code: str, until: datetime) -> tuple[Decimal, datetime, Decimal] | None:
        bars = self.bars_from_open(stock_code=stock_code, until=until)
        priced = [bar for bar in bars if bar.get("high_price") and bar.get("close_price")]
        if not priced:
            return None
        peak_bar = max(priced, key=lambda row: (Decimal(row["high_price"]), -row["bar_time"].timestamp()))
        return Decimal(peak_bar["high_price"]), peak_bar["bar_time"], Decimal(priced[-1]["close_price"])
