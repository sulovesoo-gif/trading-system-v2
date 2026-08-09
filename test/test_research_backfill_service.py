from datetime import date, datetime
import unittest

from src.service.research_backfill_service import ResearchBackfillService, OFFICIAL_PAIRS


class _Calendar:
    def open_dates(self, start, end): return [date(2026, 8, 3)]


class _Collector:
    def __init__(self): self.calls=[]
    def collect(self, **kwargs):
        self.calls.append(kwargs)
        if 'input_date' in kwargs:
            return [{"bar_time": datetime(2026,8,3,9), "stock_code":kwargs['stock_code']}]
        return []


class _Ingestion:
    def __init__(self): self.rows=[]
    def store(self, _table, rows):
        self.rows.extend(rows)
        return type('Result', (), {'requested_count': len(rows), 'inserted_count': len(rows), 'duplicate_count': 0})()


class ResearchBackfillServiceTest(unittest.TestCase):
    def test_minute_backfill_skips_closed_date_and_returns_per_day_progress(self):
        minute, daily, ingestion = _Collector(), _Collector(), _Ingestion()
        service = ResearchBackfillService(minute_collector=minute, daily_collector=daily, raw_ingestion=ingestion, calendar=_Calendar())
        result = service.backfill_minutes(stock_code='000660', start_date=date(2026,8,2), end_date=date(2026,8,3))
        self.assertEqual(result[0]['status'], 'SKIPPED_NON_TRADING')
        self.assertEqual(result[1]['status'], 'SUCCESS')
        self.assertEqual(result[1]['inserted'], 1)
        self.assertEqual(len(minute.calls), 1)

    def test_official_ten_pairs_are_declared_trade_target_first(self):
        self.assertEqual(len(OFFICIAL_PAIRS), 10)
        self.assertTrue(all(pair.trade_stock_code for pair in OFFICIAL_PAIRS))
        self.assertEqual(OFFICIAL_PAIRS[0].trade_stock_code, '000660')
