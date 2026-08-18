import unittest

from src.research_core.fingerprint import deduplicate
from src.strategy_core.registry import StrategyDefinition


def _definition(strategy_id: int, *, hold_minutes: int = 30, execution_stock: str = "0193L0") -> StrategyDefinition:
    return StrategyDefinition(
        strategy_id=strategy_id,
        strategy_code="S2_FAILED_OR_VWAP",
        strategy_instance=f"RESEARCH_STRATEGY_{strategy_id}",
        signal_stock_code="005930",
        signal_direction="SHORT",
        execution_stock_code=execution_stock,
        execution_direction="LONG",
        entry_variant="OR_30",
        exit_variant="FIXED_30",
        entry_params={"strategy_group": "S2_FAILED_OR_VWAP", "or_minutes": 30},
        exit_params={"hold_minutes": hold_minutes},
    )


class ResearchLiveEquivalentTest(unittest.TestCase):
 def test_live_equivalent_requires_full_entry_exit_and_execution_semantics(self):
    paths = deduplicate((
        _definition(623),
        _definition(624, hold_minutes=20),
        _definition(625, execution_stock="0193W0"),
    ))
    equivalent = next(path for path in paths if path.strategy_ids == (623,))
    different_exit = next(path for path in paths if path.strategy_ids == (624,))
    different_product = next(path for path in paths if path.strategy_ids == (625,))
    self.assertTrue(equivalent.live_equivalent)
    self.assertFalse(different_exit.live_equivalent)
    self.assertFalse(different_product.live_equivalent)
