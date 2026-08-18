"""Completed one-minute KIS collection into canonical RAW only."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.repository.raw_specs import RawTable
from src.service.raw_ingestion_service import RawIngestionService


class CompletedMinuteRawCollector:
    """Persist just the immediately preceding completed bar for each source.

    No SMA, notification, Strategy Core, Forward, order, or broker dependency is
    allowed in this runtime.  The RAW repository's natural-key conflict contract
    makes repeat cycles idempotent.
    """

    def __init__(self, *, collector, repository, source_registry, venue: str = "KRX") -> None:
        self.collector = collector
        self.repository = repository
        self.source_registry = source_registry
        self.venue = venue

    def run_cycle(self, *, now: datetime) -> dict[str, object]:
        target_time = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
        results: dict[str, object] = {}
        for stock_code in self.source_registry.stock_codes():
            rows = self.collector.collect(
                stock_code=stock_code,
                market_code="KOSPI",
                trading_venue=self.venue,
                input_hour=now.strftime("%H%M%S"),
            )
            completed = [row for row in rows if row.get("bar_time") == target_time]
            if len(completed) != 1:
                results[stock_code] = "NO_COMPLETED_BAR"
                continue
            results[stock_code] = RawIngestionService(self.repository).store(RawTable.STOCK_MINUTE, completed[0])
        return results
