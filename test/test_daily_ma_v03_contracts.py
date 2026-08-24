from datetime import datetime
import unittest

from src.daily_ma_v03.contracts import ActualExitKind, ExecutionBar, SignalEvent, choose_actual_exit, snapshot_payload
from src.daily_ma_v03.execution import first_actual_execution_bar
from src.daily_ma_v03.identity import snapshot_hash, transition_key


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class DailyMaV03ContractsTest(unittest.TestCase):
    def test_raw_signal_event_key_is_shared_across_exit_and_trend_variants(self):
        event = SignalEvent("005930", "LONG", 5, 20, "2026-08-24", dt("2026-08-24T15:18:00"))
        self.assertEqual(event.key(), "DAILY_MA_V03|005930|LONG|MA5_MA20|2026-08-24|2026-08-24T15:18:00|KRX")

    def test_day20_wins_only_if_strictly_before_normal_execution(self):
        normal = dt("2026-08-24T15:19:00")
        self.assertIs(choose_actual_exit(day20_trigger_time=dt("2026-08-24T15:18:00"), normal_exit_execution_time=normal), ActualExitKind.DAY20)
        self.assertIs(choose_actual_exit(day20_trigger_time=normal, normal_exit_execution_time=normal), ActualExitKind.NORMAL)
        self.assertIs(choose_actual_exit(day20_trigger_time=None, normal_exit_execution_time=normal), ActualExitKind.NORMAL)

    def test_execution_resolution_uses_first_existing_same_day_krx_bar_only(self):
        bars = [
            ExecutionBar(dt("2026-08-24T15:18:00"), 100),
            ExecutionBar(dt("2026-08-24T15:21:00"), 101),
            ExecutionBar(dt("2026-08-24T15:31:00"), 102),
        ]
        self.assertEqual(first_actual_execution_bar(bars=bars, signal_time=dt("2026-08-24T15:18:00")).time, dt("2026-08-24T15:21:00"))
        self.assertIsNone(first_actual_execution_bar(bars=[ExecutionBar(dt("2026-08-24T15:31:00"), 102)], signal_time=dt("2026-08-24T15:18:00")))

    def test_snapshot_hash_is_deterministic_and_transition_key_is_one_shot(self):
        snapshot = snapshot_payload(source_bar={"time": "15:18", "close": 100}, prior_close_hash="x", entry_fast_value=10,
                                    entry_slow_value=9, trend_value=8, trend_passed=True, direction="LONG", venue="KRX", data_source="KIS")
        self.assertEqual(snapshot_hash(snapshot), snapshot_hash(dict(snapshot)))
        self.assertNotEqual(
            transition_key(paper_trade_id=1, transition_type="DAY20_TRIGGERED", source_bar_time="2026-08-24T15:18:00"),
            transition_key(paper_trade_id=1, transition_type="NORMAL_EXIT", source_bar_time="2026-08-24T15:18:00"),
        )

