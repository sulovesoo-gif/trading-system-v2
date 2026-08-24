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


if __name__ == "__main__":
    unittest.main()
