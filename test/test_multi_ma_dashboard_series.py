from datetime import datetime, timedelta
import unittest

from scripts.dashboard.serve_multi_ma_dashboard import _contiguous_average


class DashboardSeriesTest(unittest.TestCase):
    def test_contiguous_average_does_not_skip_missing_official_minute(self):
        start = datetime(2026, 8, 3, 15, 40)
        points = [(start + timedelta(minutes=offset), 100 + offset) for offset in (0, 1, 3, 4, 5)]
        self.assertEqual(_contiguous_average(points, 3), 104.0)
        self.assertIsNone(_contiguous_average(points, 5))

    def test_contiguous_average_returns_expected_complete_window(self):
        start = datetime(2026, 8, 3, 15, 42)
        points = [(start + timedelta(minutes=offset), value) for offset, value in enumerate((1580000, 1581000, 1585000, 1587000, 1582000))]
        self.assertEqual(_contiguous_average(points, 3), 1584666.67)
        self.assertEqual(_contiguous_average(points, 5), 1583000.0)
