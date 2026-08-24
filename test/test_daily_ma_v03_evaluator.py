import unittest

from src.daily_ma_v03.evaluator import DailyMaStrategy, day20_triggered, evaluate_ma, evaluate_strategy


class DailyMaV03EvaluatorTest(unittest.TestCase):
    def test_current_ma_includes_today_1518_and_previous_does_not(self):
        evaluation = evaluate_ma(prior_closes=[10, 10, 10, 10, 10], today_1518_close=20, periods=(3, 5))
        self.assertEqual(10, evaluation.values_previous[3])
        self.assertEqual(10, evaluation.values_previous[5])
        self.assertEqual(40 / 3, evaluation.values_now[3])
        self.assertEqual(12, evaluation.values_now[5])

    def test_entry_and_normal_exit_are_independent(self):
        # A deliberately different pair allows a new entry and existing-trade exit
        # within the same 15:18 batch; runtime must not use elif.
        strategy = DailyMaStrategy("1", "005930", "0193W0", "LONG", 3, 5, 3, 5, None, True)
        evaluation = evaluate_ma(prior_closes=[10, 10, 10, 10, 10], today_1518_close=20, periods=(3, 5))
        decision = evaluate_strategy(strategy=strategy, ma=evaluation)
        self.assertTrue(decision.entry)
        self.assertFalse(decision.normal_exit)

    def test_day20_contract(self):
        self.assertTrue(day20_triggered(direction="LONG", source_close=80, previous_official_close=100))
        self.assertTrue(day20_triggered(direction="SHORT", source_close=120, previous_official_close=100))
        self.assertFalse(day20_triggered(direction="LONG", source_close=81, previous_official_close=100))

    def test_insufficient_completed_daily_closes_fails_closed(self):
        with self.assertRaises(ValueError):
            evaluate_ma(prior_closes=[10, 10], today_1518_close=10, periods=(3,))


if __name__ == "__main__":
    unittest.main()
