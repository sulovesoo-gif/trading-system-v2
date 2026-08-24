import unittest
from datetime import datetime

from src.daily_ma_v03.contracts import ActualExitKind
from src.daily_ma_v03.lifecycle import normal_delta, paper_return, select_actual_exit


class DailyMaV03LifecycleTest(unittest.TestCase):
    def test_day20_wins_only_when_trigger_precedes_normal_execution(self):
        normal = datetime(2026, 8, 24, 15, 19)
        plan = select_actual_exit(day20_trigger_time=datetime(2026, 8, 24, 15, 18),
                                  day20_execution_time=normal, day20_execution_price=80,
                                  normal_execution_time=normal, normal_execution_price=90)
        self.assertEqual(ActualExitKind.DAY20, plan.kind)
        # Same execution bar does not override strict trigger precedence.
        equal = select_actual_exit(day20_trigger_time=normal, day20_execution_time=normal,
                                   day20_execution_price=80, normal_execution_time=normal,
                                   normal_execution_price=90)
        self.assertEqual(ActualExitKind.NORMAL, equal.kind)

    def test_actual_and_normal_returns_are_kept_separate(self):
        actual = paper_return(entry_price=100, exit_price=80)
        normal = paper_return(entry_price=100, exit_price=110)
        delta = normal_delta(actual=actual, normal=normal)
        self.assertAlmostEqual(-20, actual.return_pct)
        self.assertAlmostEqual(10, normal.return_pct)
        self.assertAlmostEqual(30, delta.return_pct)

    def test_day20_actual_then_restart_then_normal_preserves_actual(self):
        # Durable-row projection: DAY20 closes actual position but leaves normal
        # tracking open; after a process restart only normal fields may change.
        row = {
            "actual_exit_time": None, "actual_exit_price": None,
            "normal_tracking_status": "OPEN", "normal_exit_time": None,
            "normal_exit_price": None,
        }
        day20 = select_actual_exit(
            day20_trigger_time=datetime(2026, 8, 24, 10, 0),
            day20_execution_time=datetime(2026, 8, 24, 10, 1), day20_execution_price=80,
            normal_execution_time=datetime(2026, 8, 24, 15, 19), normal_execution_price=110,
        )
        row["actual_exit_time"], row["actual_exit_price"] = day20.execution_time, day20.execution_price
        self.assertEqual("OPEN", row["normal_tracking_status"])
        restored = dict(row)  # represents loading the durable row after restart
        restored["normal_exit_time"], restored["normal_exit_price"] = datetime(2026, 8, 24, 15, 19), 110
        restored["normal_tracking_status"] = "CLOSED"
        self.assertEqual(datetime(2026, 8, 24, 10, 1), restored["actual_exit_time"])
        self.assertEqual(80, restored["actual_exit_price"])
        self.assertEqual(110, restored["normal_exit_price"])


if __name__ == "__main__":
    unittest.main()
