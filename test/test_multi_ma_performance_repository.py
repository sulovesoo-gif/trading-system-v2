from datetime import date
from decimal import Decimal
import unittest

from src.analysis.strategy.multi_ma_performance import Portfolio
from src.repository.multi_ma_performance_repository import MultiMaPerformanceKey, OBSERVATION_CODES, STRATEGY_CODES


class MultiMaPerformanceModelTest(unittest.TestCase):
    def key(self, strategy="ACCUMULATED", obs="SEC_05", price="CLOSE"):
        return MultiMaPerformanceKey(date(2026, 7, 30), "000660", "INTEGRATED", strategy, obs, "MA_3_5_10", price)

    def test_key_accepts_exact_12_observation_codes(self):
        self.assertEqual(len(OBSERVATION_CODES), 12)
        self.assertEqual(len(STRATEGY_CODES) * len(OBSERVATION_CODES), 48)
        self.key().values()

    def test_invalid_observation_code_is_rejected(self):
        with self.assertRaises(ValueError):
            self.key(obs="5").values()

    def test_weighted_leg_profit_and_session_close(self):
        portfolio = Portfolio(Decimal("900"))
        portfolio.enter("LONG", Decimal("100"), Decimal("0.333333333333"), "SIGNAL_1")
        portfolio.enter("LONG", Decimal("110"), Decimal("0.333333333333"), "SIGNAL_2")
        profit, _ = portfolio.close(Decimal("120"))
        self.assertGreater(profit, 0)
        self.assertEqual(portfolio.direction, "FLAT")
