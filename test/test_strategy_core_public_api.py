"""Public Core API contracts independent from any Golden fixture."""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
import unittest

from src.strategy_core import HistoricalDataProvider, HistoricalGoldenValidationAdapter, StrategyCore, strategy_from_registry_row
from src.strategy_core.bars import CompletedBar


ROOT = Path(__file__).resolve().parents[1]


def _bar(at: str, opened: float, high: float, low: float, close: float, volume: float) -> CompletedBar:
    return CompletedBar(datetime.fromisoformat(at), opened, high, low, close, volume)


class PublicCoreApiTest(unittest.TestCase):
    def test_s1_core_generates_from_bars_without_fixture_input(self) -> None:
        definition = strategy_from_registry_row({
            "strategy_id": None, "strategy_code": "S1_OR_PULLBACK_RESTART", "signal_stock_code": "005930",
            "signal_direction": "LONG", "execution_stock_code": "0193W0", "execution_direction": "LONG",
            "entry_variant": "test", "exit_variant": "test", "entry_params": {}, "exit_params": {},
        }, strategy_instance="S1")
        source = [
            _bar("2026-08-01T09:00:00", 100, 101, 99, 100, 100),
            _bar("2026-08-01T09:29:00", 100, 102, 99, 101, 100),
            _bar("2026-08-01T09:30:00", 101, 104, 100, 101, 200),  # excluded boundary
            _bar("2026-08-01T09:31:00", 101, 105, 100, 104, 200),
            _bar("2026-08-01T09:32:00", 104, 105, 101, 103, 150),
            _bar("2026-08-01T09:33:00", 103, 107, 102, 106, 180),
            _bar("2026-08-01T09:34:00", 106, 107, 105, 106, 100),
            _bar("2026-08-01T15:19:00", 106, 107, 105, 106, 100),
        ]
        core = StrategyCore(definition)
        decision = core.entry_decisions(source)[0]
        self.assertEqual(decision.signal_time, datetime.fromisoformat("2026-08-01T09:33:00"))
        self.assertEqual(decision.target_time, datetime.fromisoformat("2026-08-01T09:34:00"))

    def test_historical_adapter_maps_target_to_execution_bar_without_price_in_core(self) -> None:
        provider = HistoricalDataProvider({"0193W0": [_bar("2026-08-01T09:34:00", 20, 21, 19, 20, 1)]})
        adapter = HistoricalGoldenValidationAdapter(provider)
        self.assertEqual(adapter.execution.entry_price(adapter.execution.entry_bar(type("D", (), {"target_time": datetime.fromisoformat("2026-08-01T09:34:00"), "execution_stock_code": "0193W0"})())), 20)

    def test_core_modules_do_not_import_or_read_golden_fixture(self) -> None:
        for filename in ("engine.py", "historical.py", "replay.py"):
            tree = ast.parse((ROOT / "src" / "strategy_core" / filename).read_text(encoding="utf-8"))
            imported = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            self.assertFalse(any("fixture" in name.lower() or "golden" in name.lower() for name in imported))


if __name__ == "__main__":
    unittest.main()
