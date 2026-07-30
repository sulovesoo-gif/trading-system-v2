from datetime import datetime
import unittest

from scripts.realtime.run_skhynix_sma_cross_alert import (
    completed_rows_for_storage,
    initial_analysis_watermark,
    is_observation_time,
    new_completed_bar_times,
)


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

    def test_in_progress_bar_is_not_persisted_and_is_stored_after_next_minute(self):
        rows = [
            {"bar_time": datetime(2026, 7, 30, 9, 0), "close_price": 1383000},
            {"bar_time": datetime(2026, 7, 30, 9, 1), "close_price": 1384000},
        ]
        self.assertEqual(
            completed_rows_for_storage(rows, now=datetime(2026, 7, 30, 9, 1, 5)),
            [rows[0]],
        )
        self.assertEqual(
            completed_rows_for_storage(rows, now=datetime(2026, 7, 30, 9, 2, 5)),
            rows,
        )

    def test_restart_watermark_does_not_reprocess_prior_completed_bars(self):
        now = datetime(2026, 7, 30, 11, 20, 30)
        watermark = initial_analysis_watermark(now)
        rows = [
            {"bar_time": datetime(2026, 7, 30, 11, 19)},
            {"bar_time": datetime(2026, 7, 30, 11, 20)},
        ]
        self.assertEqual(new_completed_bar_times(rows, now=now, last_processed=watermark), [])
