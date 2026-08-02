from decimal import Decimal
import unittest

from src.analysis.event.multi_ma_event import MultiMaSignal
from src.analysis.strategy.multi_ma_strategy import StrategyState, apply_accumulated, apply_single_signal


class MultiMaStrategyTest(unittest.TestCase):
    def test_same_single_direction_is_not_duplicate_trade(self):
        state = StrategyState()
        signal = MultiMaSignal("SIGNAL_1", "LONG", "test")
        self.assertEqual(len(apply_single_signal(state, signal, accepted_type="SIGNAL_1")), 1)
        self.assertEqual(apply_single_signal(state, signal, accepted_type="SIGNAL_1"), [])

    def test_accumulated_strategy_uses_distinct_signals_only(self):
        state = StrategyState()
        self.assertEqual(apply_accumulated(state, [MultiMaSignal("SIGNAL_1", "LONG", "a")])[0].weight, Decimal("0.333333333333"))
        self.assertEqual(apply_accumulated(state, [MultiMaSignal("SIGNAL_1", "LONG", "again")]), [])
        self.assertEqual(apply_accumulated(state, [MultiMaSignal("SIGNAL_2", "LONG", "b")])[0].weight, Decimal("0.333333333333"))
        actions = apply_accumulated(state, [MultiMaSignal("SIGNAL_3", "SHORT", "reverse")])
        self.assertEqual(actions[0].action, "CLOSE")
        self.assertEqual(actions[1].weight, Decimal("0.333333333333"))
