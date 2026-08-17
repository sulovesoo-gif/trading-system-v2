from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path
import unittest

from src.live_intent import DataQualityGate, InMemoryLiveIntentStore, LiveStrategyAdapter, LiveStrategyInstance, MarketContext
from src.strategy_core import HistoricalDataProvider, strategy_from_registry_row
from src.strategy_core.bars import CompletedBar


ROOT = Path(__file__).resolve().parents[1]


def bar(at: str, close: float = 100) -> CompletedBar:
    return CompletedBar(datetime.fromisoformat(at), close, close + 1, close - 1, close, 100)


class GateTest(unittest.TestCase):
    def test_entry_blocks_unverified_gap_and_exit_reports_uncertain(self):
        bars = [bar("2026-08-01T09:00:00"), bar("2026-08-01T09:03:00")]
        gate = DataQualityGate()
        entry = gate.evaluate_entry(required_lookback=2, evaluated_at=bars[-1].time, completed_bars=bars)
        exit_result = gate.evaluate_exit(required_lookback=2, evaluated_at=bars[-1].time, completed_bars=bars)
        self.assertEqual(entry.status, "BLOCKED_DATA_GAP")
        self.assertEqual(exit_result.status, "EXIT_DATA_UNCERTAIN")

    def test_verified_no_bar_is_not_silently_called_gap(self):
        bars = [bar("2026-08-01T09:00:00"), bar("2026-08-01T09:03:00")]
        context = MarketContext(legitimate_no_bar_intervals=((bars[0].time, bars[1].time, "CIRCUIT_BREAKER"),))
        self.assertEqual(DataQualityGate().evaluate_entry(required_lookback=2, evaluated_at=bars[-1].time, completed_bars=bars, context=context).status, "LEGITIMATE_NO_BAR")


class PersistenceTest(unittest.TestCase):
    def test_idempotency_and_restart_recovery_are_durable_model(self):
        from src.live_intent.contracts import IntentType, LiveIntent, RuntimeState, RuntimeStatus
        at = datetime.fromisoformat("2026-08-01T10:00:00")
        intent = LiveIntent.build(strategy_instance_id="one", strategy_code="X", strategy_version="1", code_commit=None,
            source_decision_id="00000000-0000-0000-0000-000000000001", intent_type=IntentType.ENTRY_INTENT,
            signal_stock_code="A", signal_direction="LONG", execution_stock_code="B", execution_direction="LONG",
            signal_time=at, decision_time=at, execution_target_time=at + timedelta(minutes=1), reason_code="X",
            decision_evidence={}, data_quality_status="PASS", runtime_state_before=RuntimeStatus.FLAT, runtime_state_after=RuntimeStatus.OPEN_SIMULATED)
        store = InMemoryLiveIntentStore()
        _, first = store.create_intent_and_transition(intent, RuntimeState("one", RuntimeStatus.OPEN_SIMULATED))
        _, second = store.create_intent_and_transition(intent, RuntimeState("one", RuntimeStatus.OPEN_SIMULATED))
        self.assertTrue(first)
        self.assertFalse(second)
        store.states.clear()  # model a crash after durable intent before state write
        self.assertEqual(store.recover()["one"].status, RuntimeStatus.OPEN_SIMULATED)


class SharedEntryTest(unittest.TestCase):
    def test_grouped_variants_call_s3_entry_core_once(self):
        definition = strategy_from_registry_row({
            "strategy_id": None, "strategy_code": "S3_VOLUME_CLIMAX_REVERSAL", "signal_stock_code": "000660",
            "signal_direction": "SHORT", "execution_stock_code": "0197X0", "execution_direction": "LONG",
            "entry_variant": "test", "exit_variant": "test", "entry_params": {"rvol_threshold": 2.0}, "exit_params": {},
        })
        source = [bar(f"2026-08-01T09:{minute:02}:00") for minute in range(0, 20)]
        provider = HistoricalDataProvider({"000660": source, "0197X0": source})
        instances = (
            LiveStrategyInstance("s3-3", definition, entry_group="shared"),
            LiveStrategyInstance("s3-5", definition, entry_group="shared"),
        )
        adapter = LiveStrategyAdapter(provider=provider, instances=instances, store=InMemoryLiveIntentStore())
        adapter.process_completed_day("2026-08-01")
        self.assertEqual(adapter.entry_core_calls_by_group, {"shared": 1})


class DependencyTest(unittest.TestCase):
    def test_live_intent_has_no_order_or_broker_dependency(self):
        forbidden = ("order", "broker", "capital", "position", "kis", "collector", "ntfy")
        for path in (ROOT / "src" / "live_intent").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            imports += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            self.assertTrue(all(not any(word in name.lower() for word in forbidden) for name in imports), (path, imports))


if __name__ == "__main__":
    unittest.main()
