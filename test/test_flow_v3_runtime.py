from __future__ import annotations

import ast
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.flow_v3.engine import FlowV3SignalEngine
from src.flow_v3.models import MinuteBase, MinuteState, StrategyContract


ROOT = Path(__file__).resolve().parents[1]


def state(
    at: datetime,
    *,
    flow_cross: int | None = None,
    velocity_cross: int | None = None,
    program: str = "-10",
    program_velocity: str = "-1",
    quality: str = "OK",
    long_absorption: bool = True,
    short_absorption: bool = True,
) -> MinuteState:
    return MinuteState(
        business_date=at.date(),
        stock_code="005930",
        bar_time=at,
        underlying_close=Decimal("100"),
        aggressive_buy_amount=Decimal("20"),
        aggressive_sell_amount=Decimal("10"),
        flow_value=Decimal("10"),
        velocity_value=Decimal("2"),
        program_net_flow=Decimal(program),
        program_velocity=Decimal(program_velocity),
        long_absorption=long_absorption,
        short_absorption=short_absorption,
        snapshot_count=12,
        execution_source_gap=quality != "OK",
        execution_duplicate_rows=0,
        program_source_gap=False,
        program_duplicate_rows=0,
        orderbook_source_gap=False,
        orderbook_duplicate_rows=0,
        quality_code=quality,
        flow_averages={1: Decimal("1"), 3: Decimal("1")},
        velocity_averages={1: Decimal("1"), 3: Decimal("1")},
        flow_crosses={"01": flow_cross},
        velocity_crosses={"01": velocity_cross},
    )


def strategy(
    family: str,
    *,
    direction: str = "LONG",
    program: str = "P0",
    policy: str = "SIGNAL_EOD",
) -> StrategyContract:
    return StrategyContract(
        strategy_id=f"TEST-{family}-{direction}-{program}-{policy}",
        stock_code="005930",
        direction=direction,
        entry_family_code=family,
        entry_fast_period=1,
        entry_slow_period=3,
        program_condition_code=program,
        exit_fast_period=1,
        exit_slow_period=3,
        exit_policy_code=policy,
        execution_code="0193W0" if direction == "LONG" else "0193L0",
    )


class FlowV3EngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FlowV3SignalEngine()
        self.at = datetime(2026, 9, 8, 10, 0)

    def test_f1_f2_f3_f4_and_program_conditions(self) -> None:
        current = state(self.at, flow_cross=1, velocity_cross=1)
        lead = state(self.at - timedelta(minutes=3), velocity_cross=1)
        contracts = (
            strategy("F1", program="P0"),
            strategy("F2", program="P1"),
            strategy("F3", program="P2"),
            strategy("F4", program="P0"),
        )
        signals = self.engine.entry_signals(
            state=current, recent_states=(lead,), strategies=contracts
        )
        self.assertEqual({row.strategy.entry_family_code for row in signals}, {"F1", "F2", "F3", "F4"})
        self.assertEqual(next(row for row in signals if row.strategy.entry_family_code == "F3").velocity_lead_time, lead.bar_time)

    def test_program_condition_rejects_wrong_direction_and_gap(self) -> None:
        current = state(self.at, flow_cross=1, program="10")
        self.assertEqual(
            self.engine.entry_signals(
                state=current, recent_states=(), strategies=(strategy("F1", program="P1"),)
            ),
            (),
        )
        current = MinuteState(**{**current.__dict__, "program_net_flow": Decimal("-1"), "program_source_gap": True})
        self.assertEqual(
            self.engine.entry_signals(
                state=current, recent_states=(), strategies=(strategy("F1", program="P1"),)
            ),
            (),
        )

    def test_incomplete_minute_never_emits_signal(self) -> None:
        current = state(self.at, flow_cross=1, quality="EXECUTION_SOURCE_GAP")
        self.assertEqual(
            self.engine.entry_signals(
                state=current, recent_states=(), strategies=(strategy("F1"),)
            ),
            (),
        )

    def test_entry_key_is_official_millisecond_identity(self) -> None:
        current = state(self.at.replace(microsecond=123456), flow_cross=1)
        signal = self.engine.entry_signals(
            state=current, recent_states=(), strategies=(strategy("F1"),)
        )[0]
        self.assertTrue(signal.entry_event_key.endswith("20260908100000123"))

    def test_same_strategy_new_signal_creates_distinct_lot_identity(self) -> None:
        contract = strategy("F1")
        first = self.engine.entry_signals(
            state=state(self.at, flow_cross=1), recent_states=(), strategies=(contract,)
        )[0]
        replay = self.engine.entry_signals(
            state=state(self.at, flow_cross=1), recent_states=(), strategies=(contract,)
        )[0]
        later = self.engine.entry_signals(
            state=state(self.at + timedelta(minutes=5), flow_cross=1),
            recent_states=(),
            strategies=(contract,),
        )[0]
        self.assertEqual(first.entry_event_key, replay.entry_event_key)
        self.assertNotEqual(first.entry_event_key, later.entry_event_key)

    def test_daily_boundary_resets_velocity_and_windows(self) -> None:
        previous = state(datetime(2026, 9, 7, 15, 30))
        base = MinuteBase(
            business_date=date(2026, 9, 8),
            stock_code="005930",
            bar_time=datetime(2026, 9, 8, 9, 0),
            underlying_close=Decimal("100"),
            aggressive_buy_amount=Decimal("5"),
            aggressive_sell_amount=Decimal("1"),
            flow_value=Decimal("4"),
            program_net_flow=Decimal("2"),
        )
        result = self.engine.build_state(base=base, history=(previous,))
        self.assertIsNone(result.velocity_value)
        self.assertIsNone(result.flow_averages[3])
        self.assertIsNone(result.velocity_averages[1])

    def test_incomplete_previous_minute_breaks_runtime_series(self) -> None:
        previous = state(
            datetime(2026, 9, 8, 9, 0), quality="EXECUTION_SOURCE_GAP"
        )
        base = MinuteBase(
            business_date=date(2026, 9, 8),
            stock_code="005930",
            bar_time=datetime(2026, 9, 8, 9, 1),
            underlying_close=Decimal("100"),
            aggressive_buy_amount=Decimal("5"),
            aggressive_sell_amount=Decimal("1"),
            flow_value=Decimal("4"),
            program_net_flow=Decimal("2"),
        )
        result = self.engine.build_state(base=base, history=(previous,))
        self.assertIsNone(result.velocity_value)
        self.assertIsNone(result.flow_crosses["01"])

    def test_exit_source_is_family_specific(self) -> None:
        self.assertEqual(self.engine.exit_cross_direction(strategy("F2")), ("VELOCITY", -1))
        for family in ("F1", "F3", "F4"):
            self.assertEqual(self.engine.exit_cross_direction(strategy(family)), ("FLOW", -1))
        self.assertEqual(
            self.engine.exit_cross_direction(strategy("F1", direction="SHORT")),
            ("FLOW", 1),
        )


class FlowV3PersistenceContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repository = (ROOT / "src/flow_v3/repository.py").read_text(encoding="utf-8")
        cls.migration = (ROOT / "database/migrations/20260908_flow_v3_runtime_additive.sql").read_text(encoding="utf-8")

    def test_idempotency_and_overlap_contracts(self) -> None:
        self.assertIn("UNIQUE (strategy_id,entry_event_key)", self.migration)
        self.assertIn("ON CONFLICT (strategy_id,entry_event_key) DO NOTHING", self.repository)
        self.assertNotIn("existing open", self.repository.lower())
        self.assertIn("pg_try_advisory_lock", self.repository)
        self.assertIn("flow_v3_runtime_cursor", self.repository)

    def test_eod_and_hold_contracts(self) -> None:
        self.assertIn("bar_time::time<=TIME '15:28:00'", self.repository)
        self.assertIn("bar_time::time<=TIME '15:29:00'", self.repository)
        self.assertIn("m.exit_policy_code='SIGNAL_HOLD'", self.repository)
        self.assertIn("m.exit_policy_code='SIGNAL_EOD'", self.repository)
        self.assertNotIn("generate_series", self.repository)
        self.assertNotIn("interpolate", self.repository.lower())

    def test_raw_is_read_only_and_no_live_send_import_exists(self) -> None:
        tree = ast.parse(self.repository)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        self.assertFalse(any("broker" in name or "live" in name for name in imported))
        for raw_table in (
            "raw_flow_execution",
            "raw_flow_program",
            "raw_flow_orderbook_5s",
            "raw_stock_minute",
        ):
            self.assertNotRegex(
                self.repository,
                rf"(?is)(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+{raw_table}\b",
            )


if __name__ == "__main__":
    unittest.main()
