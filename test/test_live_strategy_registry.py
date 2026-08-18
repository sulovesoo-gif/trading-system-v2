import unittest
from decimal import Decimal

from src.live_registry import (
    LiveStrategyRegistryError,
    LiveStrategyRegistryRepository,
    LiveStrategyResolution,
    strategy_instance_id_for,
)


class LiveStrategyRegistryTest(unittest.TestCase):
    def test_instance_id_is_deterministic_and_distinct(self):
        self.assertEqual(strategy_instance_id_for(42), "LIVE_STRATEGY_42")
        self.assertEqual(strategy_instance_id_for(42), strategy_instance_id_for(42))
        self.assertNotEqual(strategy_instance_id_for(42), strategy_instance_id_for(43))
        with self.assertRaises(LiveStrategyRegistryError):
            strategy_instance_id_for(0)

    def test_disabled_registry_row_is_safe_for_phase_gated_smoke(self):
        row = LiveStrategyResolution(
            live_strategy_id=42,
            strategy_instance_id=strategy_instance_id_for(42),
            strategy_id=802,
            strategy_code="SAMSUNG_S1_LONG",
            live_name="7C_SAMSUNG_S1_LONG_SMOKE",
            live_yn="N",
            signal_stock_code="005930",
            signal_direction="LONG",
            execution_stock_code="0193W0",
            execution_direction="LONG",
            initial_live_capital=Decimal("1000000"),
            master_live_enabled_yn="Y",
        )
        self.assertTrue(row.smoke_safe)
        self.assertEqual(row.execution_stock_code, "0193W0")

    def test_repository_resolves_master_product_from_durable_row(self):
        row = (42, 802, "SAMSUNG_S1_LONG", "7C_SAMSUNG_S1_LONG_SMOKE", "N", "005930", "LONG", "0193W0", "LONG", Decimal("1000000"), "Y")

        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, *_): pass
            def fetchone(self): return row

        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def cursor(self): return Cursor()

        resolved = LiveStrategyRegistryRepository(Connection).resolve_by_id(42)
        self.assertEqual(resolved.strategy_instance_id, "LIVE_STRATEGY_42")
        self.assertEqual(resolved.strategy_id, 802)
        self.assertEqual(resolved.execution_stock_code, "0193W0")
