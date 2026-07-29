from datetime import datetime
import unittest

from scripts.realtime.run_skhynix_sma_cross_alert import is_observation_time, new_completed_bar_times


class RealtimeSmaAlertRunnerTest(unittest.TestCase):
    def test_observation_window_includes_nxt_premarket_and_aftermarket_completion(self):
        self.assertFalse(is_observation_time(datetime(2026, 7, 30, 8, 0)))
        self.assertTrue(is_observation_time(datetime(2026, 7, 30, 8, 1)))
        self.assertTrue(is_observation_time(datetime(2026, 7, 30, 20, 4)))
        self.assertFalse(is_observation_time(datetime(2026, 7, 30, 20, 5)))

    def test_only_new_completed_bars_are_returned_in_time_order(self):
        now = datetime(2026, 7, 30, 8, 14, 20)
        rows = [
            {"bar_time": datetime(2026, 7, 30, 8, 13)},
            {"bar_time": datetime(2026, 7, 30, 8, 12)},
            {"bar_time": datetime(2026, 7, 30, 8, 14)},
        ]
        self.assertEqual(
            new_completed_bar_times(rows, now=now, last_processed=datetime(2026, 7, 30, 8, 11)),
            [datetime(2026, 7, 30, 8, 12), datetime(2026, 7, 30, 8, 13)],
        )
