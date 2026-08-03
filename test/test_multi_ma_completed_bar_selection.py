from datetime import datetime
from decimal import Decimal
import unittest

from scripts.realtime.run_multi_ma_analysis import _analysis_session_gap, _has_unexpected_data_gap, _previous_completed
from src.analysis.feature.sma_feature import MinuteBar


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

    def test_data_gap_blocks_analysis_until_contiguous_completed_bars_rebuild(self):
        bar = lambda minute: MinuteBar(datetime(2026, 8, 3, 15, minute), *(Decimal("1"),) * 4)
        self.assertTrue(_has_unexpected_data_gap([bar(19), bar(26)]))
        self.assertFalse(_has_unexpected_data_gap([bar(18), bar(19)]))

    def test_close_auction_and_pre_aftermarket_are_excluded_not_data_gaps(self):
        bar = lambda hour, minute: MinuteBar(datetime(2026, 8, 3, hour, minute), *(Decimal("1"),) * 4)
        self.assertTrue(_analysis_session_gap(datetime(2026, 8, 3, 15, 20)))
        self.assertTrue(_analysis_session_gap(datetime(2026, 8, 3, 15, 30)))
        self.assertTrue(_analysis_session_gap(datetime(2026, 8, 3, 15, 39)))
        self.assertFalse(_has_unexpected_data_gap([bar(15, 19), bar(15, 40)]))
        self.assertTrue(_has_unexpected_data_gap([bar(15, 40), bar(15, 42)]))
