from datetime import datetime
from decimal import Decimal
import unittest

from src.service.stock_minute_snapshot_service import StockMinuteSnapshotService


def row(bar_time):
    return {"bar_time": bar_time, "collected_at": bar_time, "data_source": "KIS", "market_code": "KOSPI", "trading_venue": "INTEGRATED", "collect_cycle": "1MIN", "stock_code": "000660", "open_price": Decimal("1"), "high_price": Decimal("2"), "low_price": Decimal("1"), "close_price": Decimal("2"), "volume": 10, "accumulated_amount": Decimal("20"), "raw_payload": {"stck_cntg_hour": bar_time.strftime("%H%M%S")}}


class SnapshotServiceTest(unittest.TestCase):
    def test_five_second_snapshot_targets_current_minute(self):
        now = datetime(2026, 8, 3, 10, 13, 5)
        result = StockMinuteSnapshotService.build_snapshot(collector_rows=[row(datetime(2026, 8, 3, 10, 13))], observed_at=now)
        self.assertEqual(result["target_bar_time"], datetime(2026, 8, 3, 10, 13))
        self.assertEqual(result["snapshot_second"], 5)
        self.assertEqual(result["raw_payload"]["stck_cntg_hour"], "101300")

    def test_zero_second_targets_previous_minute_for_observation_only(self):
        now = datetime(2026, 8, 3, 10, 14)
        result = StockMinuteSnapshotService.build_snapshot(collector_rows=[row(datetime(2026, 8, 3, 10, 13))], observed_at=now)
        self.assertEqual(result["target_bar_time"], datetime(2026, 8, 3, 10, 13))
        self.assertEqual(result["snapshot_second"], 0)
