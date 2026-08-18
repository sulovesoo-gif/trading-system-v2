import unittest
from datetime import date, datetime, time
from decimal import Decimal

from src.live_registry import LiveStrategyResolution
from src.smoke_gate import ResolvedSmokeConfig, SmokeConfig, SmokeGate, SmokeRequest


class SmokeGateTest(unittest.TestCase):
    def test_pre_smoke_gate_never_submits(self):
        gate = SmokeGate()
        request = SmokeRequest("0197X0", "one", "BUY", 1, datetime(2026, 8, 1, 10), 0, 0)
        self.assertEqual(gate.validate(SmokeConfig("7C-1"), request)[1], "KILL_SWITCH_BLOCKED")

        config = SmokeConfig("7C-1", "0197X0", "one", time(9), time(15), True)
        self.assertEqual(
            gate.validate(config, SmokeRequest("0197X0", "one", "BUY", 2, datetime(2026, 8, 1, 10), 0, 0))[1],
            "QTY_MUST_BE_ONE",
        )
        self.assertEqual(
            gate.validate(config, SmokeRequest("0197X0", "one", "SELL", 1, datetime(2026, 8, 1, 10), 0, 0))[1],
            "PHASE_SIDE_BLOCKED",
        )
        self.assertEqual(gate.validate(config, request)[1], "NO_SUBMIT_IMPLEMENTED")

    def test_valid_fixture_is_dry_run_only(self):
        registry = LiveStrategyResolution(
            live_strategy_id=41,
            strategy_instance_id="LIVE_STRATEGY_41",
            strategy_id=802,
            strategy_code="SAMSUNG_S1_LONG",
            live_name="TEST_ONLY",
            live_yn="N",
            signal_stock_code="005930",
            signal_direction="LONG",
            execution_stock_code="0193W0",
            execution_direction="LONG",
            initial_live_capital=Decimal("1000000"),
            master_live_enabled_yn="Y",
        )
        fixture = ResolvedSmokeConfig("0193W0", registry.strategy_instance_id, date(2026, 8, 1), time(10), time(11), registry_resolution=registry)
        output = fixture.render()
        self.assertIn("DRY_RUN_ONLY", output)
        self.assertIn("network_send_enabled   = N", output)
        self.assertIn("[PASS] actual network send count == 0", output)
        self.assertIn("[PASS] actual submit count == 0", output)

    def test_invalid_contracts_are_blocked(self):
        base = dict(
            active_stock_code="0197X0",
            strategy_instance_id="TEST_ONLY",
            allowed_date=date(2026, 8, 1),
            allowed_time_from=time(10),
            allowed_time_to=time(11),
        )
        invalids = [
            {"quantity": 2},
            {"active_stock_code": "NOT_WHITELISTED"},
            {"strategy_instance_id": None},
            {"allowed_time_from": None, "allowed_time_to": None},
            {"side": "SELL"},
            {"retry_on_unknown": True},
            {"global_trade_yn": "Y"},
            {"network_send_enabled": True},
        ]
        for overrides in invalids:
            output = ResolvedSmokeConfig(**(base | overrides)).render()
            self.assertIn("RESOLVED_CONFIG_INVALID", output)
            self.assertIn("7C-1 APPROVAL BLOCKED", output)
            self.assertIn("actual_submit_count    = 0", output)


if __name__ == "__main__":
    unittest.main()
