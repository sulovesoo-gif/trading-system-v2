from datetime import datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch

from src.analysis.feature.sma_feature import MinuteBar
from src.service.provisional_daily_observation_service import RawMinute, observe
from src.service.research_complete_replay_service import ResearchSignal


def daily(index, close):
    return MinuteBar(datetime(2026, 7, 1) + timedelta(days=index), Decimal(close), Decimal(close), Decimal(close), Decimal(close))


class ProvisionalDailyObservationTest(unittest.TestCase):
    def setUp(self):
        self.history = [daily(index, 100 + index) for index in range(25)]
        self.minutes = [
            RawMinute(datetime(2026, 8, 10, 9, 0), Decimal("120"), Decimal("122"), Decimal("119"), Decimal("121"), Decimal("10")),
            RawMinute(datetime(2026, 8, 10, 9, 1), Decimal("121"), Decimal("125"), Decimal("120"), Decimal("124"), Decimal("12")),
        ]

    def test_provisional_ohlcv_and_period_ma_use_only_saved_minutes(self):
        row = observe(stock_code="000660", daily_history=self.history, minute_rows=self.minutes,
                      official_today=None, period=5, strategy_code="SIGNAL_1", entry_condition="SIGNAL_ONLY")
        self.assertEqual(row["status"], "PROVISIONAL_DAILY")
        self.assertEqual((row["open_price"], row["high_price"], row["low_price"], row["close_price"], row["volume"]),
                         (Decimal("120"), Decimal("125"), Decimal("119"), Decimal("124"), Decimal("22")))
        self.assertEqual(row["ma"], Decimal("122.8"))

    def test_period_changes_and_official_daily_has_priority(self):
        official = daily(100, "130")
        row5 = observe(stock_code="000660", daily_history=self.history, minute_rows=self.minutes,
                       official_today=official, period=5, strategy_code="SIGNAL_1", entry_condition="SIGNAL_ONLY")
        row10 = observe(stock_code="000660", daily_history=self.history, minute_rows=self.minutes,
                        official_today=official, period=10, strategy_code="SIGNAL_1", entry_condition="SIGNAL_ONLY")
        self.assertEqual(row5["status"], "DAILY_COMPLETE")
        self.assertEqual(row5["close_price"], Decimal("130"))
        self.assertNotEqual(row5["ma"], row10["ma"])

    def test_missing_and_gap_do_not_create_eligibility(self):
        missing = observe(stock_code="000660", daily_history=self.history, minute_rows=[], official_today=None,
                          period=10, strategy_code="SIGNAL_1", entry_condition="SIGNAL_ONLY")
        self.assertEqual(missing["status"], "INTRADAY_DATA_MISSING")
        gap_rows = [self.minutes[0], RawMinute(datetime(2026, 8, 10, 9, 3), Decimal("121"), Decimal("121"), Decimal("120"), Decimal("120"), Decimal("1"))]
        gap = observe(stock_code="000660", daily_history=self.history, minute_rows=gap_rows, official_today=None,
                      period=10, strategy_code="SIGNAL_1", entry_condition="SIGNAL_ONLY")
        self.assertEqual(gap["status"], "DATA_GAP")
        self.assertFalse(gap["condition_satisfied"])

    def test_all_entry_conditions_use_shared_canonical_event_without_persistence(self):
        # A synthetic shared canonical event verifies display-only eligibility;
        # observe() never receives a repository or any write capability.
        def current_long(replay, features):
            return [ResearchSignal(features[-1].bar.bar_time, "SIGNAL_1", "LONG", features[-1])]
        falling = [RawMinute(datetime(2026, 8, 10, 9, 0), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("1"))]
        with patch("src.service.provisional_daily_observation_service.DailyCompleteReplay.canonical_signals", current_long):
            signal_only = observe(stock_code="000660", daily_history=self.history, minute_rows=falling,
                                  official_today=None, period=5, strategy_code="SIGNAL_1", entry_condition="SIGNAL_ONLY")
            ma_at_signal = observe(stock_code="000660", daily_history=self.history, minute_rows=falling,
                                   official_today=None, period=5, strategy_code="SIGNAL_1", entry_condition="MA_AT_SIGNAL")
            integrated = observe(stock_code="000660", daily_history=self.history, minute_rows=falling,
                                 official_today=None, period=5, strategy_code="SIGNAL_1", entry_condition="MA_CONFIRM_INTEGRATED")
        self.assertTrue(signal_only["condition_satisfied"])
        self.assertFalse(ma_at_signal["condition_satisfied"])
        self.assertFalse(integrated["condition_satisfied"])
