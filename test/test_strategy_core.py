from __future__ import annotations

import ast
import csv
import hashlib
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from src.strategy_core.bars import CompletedBar, S1Evidence, S2Evidence
from src.strategy_core.contracts import DecisionType, SignalDecision
from src.strategy_core.historical import HistoricalDataProvider, HistoricalExecutionAdapter
from src.strategy_core.registry import strategy_from_registry_row
from src.strategy_core.state import S1State, S2State, S3State
from src.strategy_core.strategies import (
    Fixed30, PullbackLowBreakWithin30Eod, S1OrPullbackRestart, S2FailedOrVwap,
    S3VolumeClimaxReversal, StructureExitMax30Stop25,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "test" / "fixtures" / "strategy_golden"


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def bar(value: str, *, open=100, high=101, low=99, close=100, volume=100) -> CompletedBar:
    return CompletedBar(dt(value), open, high, low, close, volume)


def definition(code: str, *, instance: str | None = None, entry=None, exit=None):
    return strategy_from_registry_row({
        "strategy_id": 1, "strategy_code": code, "signal_stock_code": "000660" if code.startswith("S3") else "005930",
        "signal_direction": "SHORT" if code.startswith("S2") or code.startswith("S3") else "LONG",
        "execution_stock_code": "0197X0" if code.startswith("S3") else "0193L0",
        "execution_direction": "LONG", "entry_variant": "test", "exit_variant": "test",
        "entry_params": entry or {}, "exit_params": exit or {},
    }, strategy_instance=instance or code, code_commit="test")


class GoldenArtifactTest(unittest.TestCase):
    def test_csv_json_manifest_are_immutable_and_consistent(self):
        manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        for name, expected_hash in manifest["files"].items():
            self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), expected_hash)
        with (FIXTURES / "strategy_golden_final_v1.0.0.csv").open(encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        payload = json.loads((FIXTURES / "strategy_golden_final_v1.0.0.json").read_text(encoding="utf-8"))
        self.assertEqual(len(csv_rows), manifest["row_count"])
        self.assertEqual(len(payload["trades"]), manifest["row_count"])
        normalize = lambda value: datetime.fromisoformat(value.replace(" ", "T")).isoformat()
        csv_keys = {(r["strategy_instance"], normalize(r["signal_time"]), normalize(r["entry_time"]), normalize(r["exit_time"])) for r in csv_rows}
        json_keys = {(r["strategy_instance"], normalize(r["signal_time"]), normalize(r["entry_time"]), normalize(r["exit_time"])) for r in payload["trades"]}
        self.assertEqual(csv_keys, json_keys)
        self.assertEqual(len(csv_keys), 33)
        counts = {}
        for row in csv_rows:
            counts[row["strategy_instance"]] = counts.get(row["strategy_instance"], 0) + 1
        self.assertEqual(counts, manifest["strategy_counts"])
        three = {row["shared_entry_group"]: (normalize(row["signal_time"]), normalize(row["entry_time"])) for row in csv_rows if row["strategy_instance"] == "HYNIX_S3_SHORT_3BAR"}
        five = {row["shared_entry_group"]: (normalize(row["signal_time"]), normalize(row["entry_time"])) for row in csv_rows if row["strategy_instance"] == "HYNIX_S3_SHORT_5BAR"}
        self.assertEqual(three, five)


class DecisionCoreTest(unittest.TestCase):
    def test_s1_eod_exit_uses_last_same_day_execution_bar_when_1519_is_missing(self):
        decision = SignalDecision(
            decision_id="s1-eod", strategy_id=802, strategy_code="S1_OR_PULLBACK_RESTART",
            strategy_version="1.0.0", code_commit="test", signal_stock_code="005930",
            signal_direction="LONG", signal_time=dt("2026-08-01T15:19:00"),
            execution_stock_code="0193W0", execution_direction="LONG", decision_type=DecisionType.EXIT,
            exit_reason="EOD_1519", target_time=dt("2026-08-01T15:19:00"),
        )
        provider = HistoricalDataProvider({"0193W0": (
            bar("2026-08-01T15:18:00", close=101),
            bar("2026-08-02T09:00:00", close=102),
        )})
        execution = HistoricalExecutionAdapter(provider)
        resolved = execution.exit_bar(decision, eod_uses_close=True)
        self.assertEqual(resolved.time, dt("2026-08-01T15:18:00"))

    def test_registry_mapping_keeps_signal_and_execution_coordinates_separate(self):
        item = definition("S3_VOLUME_CLIMAX_REVERSAL", instance="HYNIX_S3_SHORT_3BAR")
        self.assertEqual((item.signal_stock_code, item.signal_direction), ("000660", "SHORT"))
        self.assertEqual((item.execution_stock_code, item.execution_direction), ("0197X0", "LONG"))

    def test_s1_pullback_entry_then_only_within_30_break_or_eod(self):
        item = definition("S1_OR_PULLBACK_RESTART")
        core = S1OrPullbackRestart(item)
        entered = core.on_evidence(S1Evidence(bar("2026-08-01T09:45:00"), 110, 90, True, True, True, 95))
        self.assertEqual(entered.decision_type, DecisionType.ENTRY)
        exit_policy = PullbackLowBreakWithin30Eod(item, entry_time=entered.signal_time, pullback_low=95)
        self.assertEqual(exit_policy.on_completed_source_bar(bar("2026-08-01T10:16:00", close=94)).decision_type, DecisionType.HOLD)
        exited = exit_policy.on_completed_source_bar(bar("2026-08-01T15:19:00", close=94))
        self.assertEqual((exited.decision_type, exited.exit_reason, exited.target_time.strftime("%H:%M")), (DecisionType.EXIT, "EOD_1519", "15:19"))
        self.assertNotIn("price", exited.evidence)

    def test_s2_fixed_30_emits_time_only_exit(self):
        item = definition("S2_FAILED_OR_VWAP")
        core = S2FailedOrVwap(item)
        entered = core.on_evidence(S2Evidence(bar("2026-08-01T09:48:00"), 110, 90, 100, True, True))
        self.assertEqual(entered.decision_type, DecisionType.ENTRY)
        exit_policy = Fixed30(item, entry_time=entered.signal_time + timedelta(minutes=1))
        self.assertEqual(exit_policy.on_time(dt("2026-08-01T10:19:00")).target_time, dt("2026-08-01T10:19:00"))

    def test_s3_shared_entry_identity_for_3bar_and_5bar_instances(self):
        entry_params = {"move_threshold": .008, "rvol_threshold": 2.0}
        bars = [bar(f"2026-08-01T09:{10+i:02}:00", close=100, open=100) for i in range(5)]
        climax = bar("2026-08-01T09:15:00", open=100, high=102, close=101, volume=300)
        confirm = bar("2026-08-01T09:16:00", open=101, high=101, close=99, volume=200)
        d3 = definition("S3_VOLUME_CLIMAX_REVERSAL", instance="HYNIX_S3_SHORT_3BAR", entry=entry_params)
        d5 = definition("S3_VOLUME_CLIMAX_REVERSAL", instance="HYNIX_S3_SHORT_5BAR", entry=entry_params)
        c3, c5 = S3VolumeClimaxReversal(d3), S3VolumeClimaxReversal(d5)
        c3.on_completed_bar(climax, prior_bars=bars, rvol20=2.0)
        c5.on_completed_bar(climax, prior_bars=bars, rvol20=2.0)
        left = c3.on_completed_bar(confirm, prior_bars=bars + [climax], rvol20=2.0)
        right = c5.on_completed_bar(confirm, prior_bars=bars + [climax], rvol20=2.0)
        self.assertEqual(left.decision_type, DecisionType.ENTRY)
        self.assertEqual(left.shared_entry_decision_id, right.shared_entry_decision_id)
        self.assertEqual(left.signal_time, right.signal_time)

    def test_s3_structure_wins_tie_and_stop_uses_execution_product_close(self):
        item = definition("S3_VOLUME_CLIMAX_REVERSAL")
        policy = StructureExitMax30Stop25(item, dt("2026-08-01T10:00:00"), 100, 3)
        signal = bar("2026-08-01T10:05:00", close=110)
        execution = bar("2026-08-01T10:05:00", close=97)
        prior = [bar("2026-08-01T10:02:00", high=105), bar("2026-08-01T10:03:00", high=106), bar("2026-08-01T10:04:00", high=107)]
        decision = policy.on_bars(signal_bar=signal, execution_bar=execution, prior_signal_bars=prior)
        self.assertEqual((decision.decision_type, decision.exit_reason), (DecisionType.EXIT, "STRUCTURE_RECLAIM"))
        self.assertEqual(decision.target_time, dt("2026-08-01T10:05:00"))

    def test_states_are_serializable_and_deterministic(self):
        self.assertEqual(S1State(pullback_low=10).serialize()["pullback_low"], 10)
        self.assertEqual(S2State(vwap=10).serialize()["vwap"], 10)
        self.assertEqual(S3State(climax_time=dt("2026-08-01T09:10:00")).serialize()["climax_time"], "2026-08-01T09:10:00")


class DependencyTest(unittest.TestCase):
    def test_core_has_no_runtime_or_trading_dependencies(self):
        forbidden = ("collector", "repository", "database", "order", "position", "capital", "dashboard", "ntfy", "email", "service")
        for path in (ROOT / "src" / "strategy_core").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            modules = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import): modules.extend(alias.name for alias in node.names)
                if isinstance(node, ast.ImportFrom): modules.append(node.module or "")
            self.assertTrue(all(not any(token in module.lower() for token in forbidden) for module in modules), (path, modules))


if __name__ == "__main__":
    unittest.main()
