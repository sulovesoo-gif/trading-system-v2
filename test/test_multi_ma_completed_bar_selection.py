from datetime import datetime
from decimal import Decimal
import unittest

from scripts.realtime.run_multi_ma_analysis import _previous_completed


class CompletedMinuteSelectionTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 3, 15, 20, 1)

    def row(self, *, volume=1, open_price="1569000", high_price="1570000", low_price="1567000", close_price="1568000"):
        return {
            "bar_time": datetime(2026, 8, 3, 15, 19),
            "open_price": Decimal(open_price), "high_price": Decimal(high_price),
            "low_price": Decimal(low_price), "close_price": Decimal(close_price), "volume": volume,
        }

    def test_selects_only_exact_expected_raw_timestamp(self):
        self.assertEqual(_previous_completed([self.row()], self.now)["bar_time"], datetime(2026, 8, 3, 15, 19))

    def test_rejects_flat_zero_volume_preliminary_placeholder(self):
        self.assertIsNone(_previous_completed([self.row(volume=0, open_price="1570000", high_price="1570000", low_price="1570000", close_price="1570000")], self.now))

    def test_rejects_row_when_expected_timestamp_is_absent(self):
        row = self.row(); row["bar_time"] = datetime(2026, 8, 3, 15, 18)
        self.assertIsNone(_previous_completed([row], self.now))

