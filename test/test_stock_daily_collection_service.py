from datetime import date
import unittest

from src.repository.common_code_repository import CommonCodeRepository, StockDailyConfig
from src.service.stock_daily_collection_service import StockDailyCollectionService
from src.service.stock_daily_backfill_service import StockDailyBackfillResult


class _Codes:
    def __init__(self, targets): self.targets = targets
    def enabled_daily_stocks(self): return self.targets


class _Calendar:
    def __init__(self, dates): self.dates = dates
    def open_dates(self, start, end): return self.dates


class _Backfill:
    def __init__(self, failures=(), inserted=0, duplicates=1):
        self.calls = []; self.failures = set(failures); self.inserted = inserted; self.duplicates = duplicates
    def run_target(self, *, target, end_date):
        self.calls.append((target, end_date))
        if target.stock_code in self.failures: raise RuntimeError("collector failed")
        return StockDailyBackfillResult(target, 1, self.inserted, self.duplicates, end_date, end_date)


class _CommonCodeProbe(CommonCodeRepository):
    def __init__(self): pass
    def _fetchall(self, sql):
        self.sql = sql
        return [("000660", "SK hynix", "KOSPI", "KRX")]


class StockDailyCollectionTest(unittest.TestCase):
    targets = [
        StockDailyConfig("000660", "SK hynix", "KOSPI", "KRX"),
        StockDailyConfig("0193T0", "Leverage", "KOSPI", "KRX"),
    ]

    def test_common_code_repository_reads_stock_daily_enabled_targets(self):
        probe = _CommonCodeProbe()
        self.assertEqual(probe.enabled_daily_stocks(), [StockDailyConfig("000660", "SK hynix", "KOSPI", "KRX")])
        self.assertIn("group_cd='STOCK_DAILY' AND use_yn='Y'", probe.sql)

    def test_existing_raw_is_idempotent_and_failures_are_isolated(self):
        backfill = _Backfill(failures={"0193T0"})
        service = StockDailyCollectionService(
            code_repository=_Codes(self.targets), calendar=_Calendar([date(2026, 8, 7)]), backfill_service=backfill
        )
        result = service.collect_trade_date(trading_date=date(2026, 8, 7))
        self.assertEqual([(item.stock_code, item.status, item.inserted_count, item.duplicate_count) for item in result], [
            ("000660", "OK", 0, 1), ("0193T0", "FAILED", 0, 0),
        ])
        self.assertEqual(len(backfill.calls), 2)

    def test_non_trading_day_skips_kis_daily_collector_for_all_targets(self):
        backfill = _Backfill()
        service = StockDailyCollectionService(
            code_repository=_Codes(self.targets), calendar=_Calendar([]), backfill_service=backfill
        )
        result = service.collect_trade_date(trading_date=date(2026, 8, 8))
        self.assertTrue(all(item.status == "NON_TRADING_DAY" for item in result))
        self.assertEqual(backfill.calls, [])

    def test_missing_trading_day_is_stored_once(self):
        backfill = _Backfill(inserted=1, duplicates=0)
        service = StockDailyCollectionService(
            code_repository=_Codes(self.targets[:1]), calendar=_Calendar([date(2026, 8, 7)]), backfill_service=backfill
        )
        result = service.collect_trade_date(trading_date=date(2026, 8, 7))
        self.assertEqual((result[0].status, result[0].inserted_count, result[0].duplicate_count), ("OK", 1, 0))
