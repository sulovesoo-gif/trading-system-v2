from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest

from src.collector.raw.domestic_stock.stock_historical_minute_collector import StockHistoricalMinuteCollector
from src.repository.backfill_repository import BackfillSegment
from src.repository.raw_repository import RawWriteResult
from src.repository.raw_specs import RawTable
from src.service.stock_minute_backfill_service import StockMinuteBackfillService, StockMinuteBackfillTarget


NOW = datetime(2026, 7, 29, 10, 30)


class FakeClient:
    def __init__(self, payload, headers=None):
        self.payload = payload
        self.last_response_headers = headers or {}
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


class SequenceClient(FakeClient):
    def __init__(self, payloads):
        super().__init__({})
        self.payloads = list(payloads)

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self.payloads.pop(0)


class FakeIngestionService:
    def __init__(self):
        self.calls = []

    def store(self, table, rows):
        self.calls.append((table, rows))
        return RawWriteResult(table.value, len(rows), len(rows), 0)


class FakeBackfillRepository:
    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.events.append((name, args, kwargs))
        return record


def minute_output(hour="101500"):
    return {
        "stck_bsop_date": "20260728",
        "stck_cntg_hour": hour,
        "stck_oprc": "10",
        "stck_hgpr": "12",
        "stck_lwpr": "9",
        "stck_prpr": "11",
        "cntg_vol": "2",
        "acml_tr_pbmn": "22",
    }


class StockHistoricalMinuteCollectorTest(unittest.TestCase):
    def test_maps_output2_rows_and_uses_krx_request(self):
        client = FakeClient({"output1": {"stck_prdy_clpr": "99000"}, "output2": [minute_output(), minute_output("101600")]})
        rows = StockHistoricalMinuteCollector(client, now_provider=lambda: NOW).collect(
            stock_code="000660",
            market_code="KOSPI",
            trading_venue="KRX",
            input_date="20260728",
            input_hour="153000",
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["trading_venue"], "KRX")
        self.assertEqual(rows[0]["bar_time"], datetime(2026, 7, 28, 10, 15))
        self.assertEqual(rows[1]["close_price"], Decimal("11"))
        self.assertEqual(rows[0]["raw_payload"], minute_output())
        self.assertEqual(client.calls[0]["tr_id"], "FHKST03010230")
        self.assertEqual(client.calls[0]["params"]["FID_COND_MRKT_DIV_CODE"], "J")
        self.assertEqual(rows[0]["previous_close_price"], Decimal("99000"))

    def test_uses_next_continuation_header_only_when_requested(self):
        client = FakeClient({"output2": []})
        StockHistoricalMinuteCollector(client, now_provider=lambda: NOW).collect(
            stock_code="000660", market_code="KOSPI", trading_venue="KRX",
            input_date="20260728", input_hour="153000", continuation="N",
        )
        self.assertEqual(client.calls[0]["extra_headers"], {"tr_cont": "N"})

    def test_rejects_unknown_trading_venue(self):
        with self.assertRaises(ValueError):
            StockHistoricalMinuteCollector(FakeClient({"output2": []}), now_provider=lambda: NOW).collect(
                stock_code="000660", market_code="KOSPI", trading_venue="UNKNOWN",
                input_date="20260728", input_hour="153000",
            )


class StockMinuteBackfillServiceTest(unittest.TestCase):
    def test_stores_page_and_records_completion(self):
        collector = StockHistoricalMinuteCollector(
            FakeClient({"output2": [minute_output()]}), now_provider=lambda: NOW
        )
        ingestion = FakeIngestionService()
        repository = FakeBackfillRepository()
        service = StockMinuteBackfillService(
            collector=collector, ingestion_service=ingestion, backfill_repository=repository
        )
        service.run_segment(
            segment=BackfillSegment(7, 1, "000660", "KRX", date(2026, 7, 28), 1),
            target=StockMinuteBackfillTarget("000660", "KOSPI"),
            trade_date=date(2026, 7, 28),
        )
        self.assertEqual(ingestion.calls[0][0], RawTable.STOCK_MINUTE)
        self.assertEqual(collector.client.calls[0]["params"]["FID_PW_DATA_INCU_YN"], "N")
        self.assertEqual([event[0] for event in repository.events], ["mark_running", "record_page", "mark_completed"])

    def test_full_page_uses_oldest_bar_as_next_time_cursor(self):
        latest = datetime(2026, 7, 28, 15, 30)
        first_page = []
        for offset in range(120):
            current = latest - timedelta(minutes=offset)
            item = minute_output(current.strftime("%H%M%S"))
            item["stck_bsop_date"] = current.strftime("%Y%m%d")
            first_page.append(item)
        client = SequenceClient([{"output2": first_page}, {"output2": [minute_output("132900")]}])
        collector = StockHistoricalMinuteCollector(client, now_provider=lambda: NOW)
        ingestion = FakeIngestionService()
        repository = FakeBackfillRepository()
        StockMinuteBackfillService(
            collector=collector, ingestion_service=ingestion, backfill_repository=repository
        ).run_segment(
            segment=BackfillSegment(8, 1, "000660", "KRX", date(2026, 7, 28), 1),
            target=StockMinuteBackfillTarget("000660", "KOSPI"),
            trade_date=date(2026, 7, 28),
        )
        self.assertEqual(len(ingestion.calls), 2)
        self.assertEqual(client.calls[1]["params"]["FID_INPUT_HOUR_1"], "133000")

    def test_resume_uses_recorded_cursor(self):
        collector = StockHistoricalMinuteCollector(
            FakeClient({"output2": [minute_output("132900")]}), now_provider=lambda: NOW
        )
        service = StockMinuteBackfillService(
            collector=collector, ingestion_service=FakeIngestionService(), backfill_repository=FakeBackfillRepository()
        )
        service.run_segment(
            segment=BackfillSegment(9, 1, "000660", "KRX", date(2026, 7, 28), 1, "20260728", "133000"),
            target=StockMinuteBackfillTarget("000660", "KOSPI"),
            trade_date=date(2026, 7, 28),
        )
        self.assertEqual(collector.client.calls[0]["params"]["FID_INPUT_HOUR_1"], "133000")

    def test_failed_segment_is_recorded_for_retry(self):
        class FailingCollector:
            def collect(self, **_kwargs):
                raise RuntimeError("temporary API failure")

        repository = FakeBackfillRepository()
        service = StockMinuteBackfillService(
            collector=FailingCollector(), ingestion_service=FakeIngestionService(), backfill_repository=repository
        )
        with self.assertRaises(RuntimeError):
            service.run_segment(
                segment=BackfillSegment(10, 1, "000660", "KRX", date(2026, 7, 28), 1),
                target=StockMinuteBackfillTarget("000660", "KOSPI"),
                trade_date=date(2026, 7, 28),
            )
        self.assertEqual([event[0] for event in repository.events], ["mark_running", "mark_failed"])

    def test_post_close_api_rows_are_not_persisted(self):
        rows = [minute_output("153000"), minute_output("153100")]
        collector = StockHistoricalMinuteCollector(FakeClient({"output2": rows}), now_provider=lambda: NOW)
        ingestion = FakeIngestionService()
        StockMinuteBackfillService(
            collector=collector, ingestion_service=ingestion, backfill_repository=FakeBackfillRepository()
        ).run_segment(
            segment=BackfillSegment(11, 1, "000660", "KRX", date(2026, 7, 28), 1),
            target=StockMinuteBackfillTarget("000660", "KOSPI"),
            trade_date=date(2026, 7, 28),
        )
        persisted = ingestion.calls[0][1]
        self.assertEqual([item["bar_time"].time().isoformat() for item in persisted], ["15:30:00"])
