from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest

from src.collector.raw.futures.futures_minute_collector import FuturesMinuteCollector
from src.repository.backfill_repository import BackfillSegment
from src.repository.raw_repository import RawWriteResult
from src.repository.raw_specs import RawTable
from src.service.futures_minute_backfill_service import FuturesMinuteBackfillService, FuturesMinuteBackfillTarget


NOW = datetime(2026, 7, 29, 10, 30)


class FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return self.payloads.pop(0)


class FakeIngestion:
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


def output_at(value: datetime) -> dict[str, str]:
    return {
        "stck_bsop_date": value.strftime("%Y%m%d"),
        "stck_cntg_hour": value.strftime("%H%M%S"),
        "futs_oprc": "10", "futs_hgpr": "12", "futs_lwpr": "9",
        "futs_prpr": "11", "cntg_vol": "2", "acml_tr_pbmn": "22",
    }


class FuturesMinuteBackfillServiceTest(unittest.TestCase):
    def target(self):
        return FuturesMinuteBackfillTarget("A01609")

    def segment(self, cursor_date=None, cursor_time=None):
        return BackfillSegment(11, 7, "A01609", "KRX", date(2026, 6, 11), 1, cursor_date, cursor_time)

    def test_collects_futures_page_with_fixed_kis_parameters(self):
        client = FakeClient([{"output2": [output_at(datetime(2026, 6, 11, 15, 45))]}])
        collector = FuturesMinuteCollector(client, now_provider=lambda: NOW)
        ingestion, repository = FakeIngestion(), FakeBackfillRepository()
        FuturesMinuteBackfillService(
            collector=collector, ingestion_service=ingestion, backfill_repository=repository
        ).run_segment(segment=self.segment(), target=self.target(), trade_date=date(2026, 6, 11))
        params = client.calls[0]["params"]
        self.assertEqual(params["FID_COND_MRKT_DIV_CODE"], "F")
        self.assertEqual(params["FID_HOUR_CLS_CODE"], "60")
        self.assertEqual(params["FID_PW_DATA_INCU_YN"], "Y")
        self.assertEqual(params["FID_FAKE_TICK_INCU_YN"], "N")
        self.assertEqual(ingestion.calls[0][0], RawTable.FUTURES_MINUTE)
        self.assertEqual(ingestion.calls[0][1][0]["futures_code"], "A01609")
        self.assertEqual(ingestion.calls[0][1][0]["trading_venue"], "KRX")
        self.assertEqual(ingestion.calls[0][1][0]["close_price"], Decimal("11"))

    def test_full_page_moves_to_minute_before_oldest_bar(self):
        latest = datetime(2026, 6, 11, 15, 45)
        full = [output_at(latest - timedelta(minutes=offset)) for offset in range(102)]
        client = FakeClient([{"output2": full}, {"output2": [output_at(datetime(2026, 6, 11, 13, 53))]}])
        service = FuturesMinuteBackfillService(
            collector=FuturesMinuteCollector(client, now_provider=lambda: NOW),
            ingestion_service=FakeIngestion(), backfill_repository=FakeBackfillRepository(),
        )
        service.run_segment(segment=self.segment(), target=self.target(), trade_date=date(2026, 6, 11))
        self.assertEqual(client.calls[1]["params"]["FID_INPUT_HOUR_1"], "140300")

    def test_empty_response_marks_segment_failed(self):
        repository = FakeBackfillRepository()
        service = FuturesMinuteBackfillService(
            collector=FuturesMinuteCollector(FakeClient([{"output2": []}]), now_provider=lambda: NOW),
            ingestion_service=FakeIngestion(), backfill_repository=repository,
        )
        with self.assertRaisesRegex(RuntimeError, "빈 목록"):
            service.run_segment(segment=self.segment(), target=self.target(), trade_date=date(2026, 6, 11))
        self.assertEqual([event[0] for event in repository.events], ["mark_running", "mark_failed"])

    def test_rejects_other_trade_date_without_storing(self):
        repository, ingestion = FakeBackfillRepository(), FakeIngestion()
        service = FuturesMinuteBackfillService(
            collector=FuturesMinuteCollector(FakeClient([{"output2": [output_at(datetime(2026, 6, 10, 15, 45))]}]), now_provider=lambda: NOW),
            ingestion_service=ingestion, backfill_repository=repository,
        )
        with self.assertRaisesRegex(RuntimeError, "요청 거래일 외"):
            service.run_segment(segment=self.segment(), target=self.target(), trade_date=date(2026, 6, 11))
        self.assertEqual(ingestion.calls, [])
