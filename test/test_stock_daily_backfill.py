from datetime import date
from decimal import Decimal
import unittest

from src.repository.raw_repository import RawWriteResult
from src.repository.raw_specs import RawTable
from src.service.stock_daily_backfill_service import (
    StockDailyBackfillService,
    StockDailyBackfillTarget,
    split_date_ranges,
)


def row(trade_date: date) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "stock_code": "000660",
        "open_price": Decimal("1"),
        "high_price": Decimal("2"),
        "low_price": Decimal("1"),
        "close_price": Decimal("2"),
        "volume": 1,
        "amount": Decimal("2"),
        "raw_payload": {"stck_bsop_date": trade_date.strftime("%Y%m%d")},
    }


class FakeCollector:
    def __init__(self) -> None:
        self.calls = []

    def collect(self, **kwargs):
        self.calls.append(kwargs)
        return [
            row(date(2026, 1, 1)),
            row(date(2026, 3, 31)),
            row(date(2026, 7, 30)),  # 요청 종료일 밖이면 저장하지 않는다.
        ]


class FakeIngestion:
    def __init__(self) -> None:
        self.stored = []

    def store(self, table, rows):
        self.stored.append((table, rows))
        return RawWriteResult(table.value, len(rows), len(rows), 0)


class StockDailyBackfillTest(unittest.TestCase):
    def test_splits_long_ranges_without_assuming_api_response_limit(self):
        self.assertEqual(
            list(split_date_ranges(date(2026, 1, 1), date(2026, 4, 1), max_days=90)),
            [(date(2026, 1, 1), date(2026, 3, 31)), (date(2026, 4, 1), date(2026, 4, 1))],
        )

    def test_stores_only_completed_requested_dates_and_aggregates_results(self):
        collector = FakeCollector()
        ingestion = FakeIngestion()
        target = StockDailyBackfillTarget("000660", "KOSPI", "KRX", date(2026, 1, 1))
        result = StockDailyBackfillService(collector=collector, ingestion_service=ingestion).run_target(
            target=target, end_date=date(2026, 3, 31), max_days_per_request=90
        )
        self.assertEqual(len(collector.calls), 1)
        self.assertEqual(ingestion.stored[0][0], RawTable.STOCK_DAILY)
        self.assertEqual([item["trade_date"] for item in ingestion.stored[0][1]], [date(2026, 1, 1), date(2026, 3, 31)])
        self.assertEqual(result.requested_count, 2)
        self.assertEqual(result.inserted_count, 2)
        self.assertEqual(result.maximum_trade_date, date(2026, 3, 31))

    def test_empty_target_range_does_not_call_collector(self):
        collector = FakeCollector()
        ingestion = FakeIngestion()
        target = StockDailyBackfillTarget("000660", "KOSPI", "KRX", date(2026, 1, 2))
        result = StockDailyBackfillService(collector=collector, ingestion_service=ingestion).run_target(
            target=target, end_date=date(2026, 1, 1)
        )
        self.assertEqual(collector.calls, [])
        self.assertEqual(result.requested_count, 0)

    def test_waits_between_multiple_api_ranges_only(self):
        collector = FakeCollector()
        ingestion = FakeIngestion()
        pauses = []
        target = StockDailyBackfillTarget("000660", "KOSPI", "KRX", date(2026, 1, 1))
        StockDailyBackfillService(
            collector=collector, ingestion_service=ingestion, sleep=pauses.append
        ).run_target(
            target=target, end_date=date(2026, 4, 1), max_days_per_request=90,
            request_interval_seconds=1.0,
        )
        self.assertEqual(pauses, [1.0])
