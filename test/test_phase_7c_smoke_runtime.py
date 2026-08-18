import unittest
from datetime import date, datetime, time
from decimal import Decimal
from threading import Barrier, Thread

from src.broker import BrokerMode, KisBrokerAdapter
from src.live_registry import LiveStrategyResolution
from src.smoke_gate import ResolvedSmokeConfig
from src.smoke_send import (
    ActualApproval,
    ApprovalStatus,
    DeterministicTransport,
    InMemorySmokeApprovalStore,
    Phase7CSmokeRuntime,
    SmokeGateState,
)


class Phase7CSmokeRuntimeTest(unittest.TestCase):
    at = datetime(2026, 8, 19, 10, 5)

    def config(self, **changes):
        registry = LiveStrategyResolution(
            2, "LIVE_STRATEGY_2", 802, "SAMSUNG_S1_LONG", "TEST", "N",
            "005930", "LONG", "0193W0", "LONG", Decimal("1000000"), "Y",
        )
        values = dict(
            active_stock_code="0193W0", strategy_instance_id="LIVE_STRATEGY_2",
            allowed_date=self.at.date(), allowed_time_from=time(10), allowed_time_to=time(10, 10),
            registry_resolution=registry,
        )
        values.update(changes)
        return ResolvedSmokeConfig(**values)

    def approved(self, status=ApprovalStatus.APPROVED_FOR_ONE_SUBMIT):
        return ActualApproval("00000000-0000-0000-0000-000000000007", "LIVE_STRATEGY_2", "0193W0", self.at.date(), time(10), time(10, 10), status)

    def runtime(self, outcome="ACK", approval=None):
        store = InMemorySmokeApprovalStore()
        if approval is not None:
            store.save(approval)
        transport = DeterministicTransport(outcome)
        adapter = KisBrokerAdapter(mode=BrokerMode.PHASE_7C_SMOKE_SEND, account="stub", whitelist={"0193W0"}, phase_7c_transport=transport)
        return Phase7CSmokeRuntime(approvals=store, adapter=adapter), store, transport, adapter

    @staticmethod
    def state(**changes):
        values = dict(global_trade_yn="N", today_actual_submit_count=0, open_order_count=0, unknown_order_count=0)
        values.update(changes)
        return SmokeGateState(**values)

    def test_no_approval_time_window_and_quantity_never_send(self):
        runtime, _, transport, adapter = self.runtime()
        _, reason = runtime.submit_once(config=self.config(), approval_id="missing", at=self.at, state=self.state())
        self.assertEqual(reason, "ACTUAL_APPROVAL_REQUIRED")
        runtime, store, transport, adapter = self.runtime(approval=self.approved())
        _, reason = runtime.submit_once(config=self.config(), approval_id=self.approved().approval_id, at=datetime(2026, 8, 19, 9, 59), state=self.state())
        self.assertEqual(reason, "TIME_WINDOW_BLOCKED")
        _, reason = runtime.submit_once(config=self.config(quantity=2), approval_id=self.approved().approval_id, at=self.at, state=self.state())
        self.assertEqual(reason, "RESOLVED_CONFIG_INVALID")
        self.assertEqual(transport.send_calls, 0)
        self.assertEqual(adapter.network_send_calls, 0)

    def test_one_submit_consumes_before_send_and_duplicate_is_blocked(self):
        approval = self.approved()
        runtime, store, transport, adapter = self.runtime(approval=approval)
        response, reason = runtime.submit_once(config=self.config(), approval_id=approval.approval_id, at=self.at, state=self.state())
        self.assertEqual(reason, "ACK")
        self.assertEqual(response["odno"], "STUB-ACK")
        self.assertEqual(transport.send_calls, 1)
        self.assertEqual(adapter.network_send_calls, 1)
        self.assertEqual(store.get(approval.approval_id).status, ApprovalStatus.CONSUMED)
        _, reason = runtime.submit_once(config=self.config(), approval_id=approval.approval_id, at=self.at, state=self.state())
        self.assertEqual(reason, "ACTUAL_APPROVAL_REQUIRED")
        self.assertEqual(transport.send_calls, 1)

    def test_timeout_restart_and_duplicate_key_do_not_resend(self):
        approval = self.approved()
        runtime, store, transport, adapter = self.runtime("TIMEOUT", approval)
        _, reason = runtime.submit_once(config=self.config(), approval_id=approval.approval_id, at=self.at, state=self.state())
        self.assertEqual(reason, "UNKNOWN_BROKER_STATE")
        self.assertEqual(store.get(approval.approval_id).status, ApprovalStatus.CONSUMED)
        self.assertEqual(store.get(approval.approval_id).broker_state, "UNKNOWN_BROKER_STATE")
        self.assertEqual(transport.send_calls, 1)
        restarted = Phase7CSmokeRuntime(approvals=store, adapter=adapter)
        self.assertEqual(restarted.recover(approval.approval_id)["status"], "UNKNOWN")
        self.assertEqual(transport.lookup_calls, 1)
        _, reason = restarted.submit_once(config=self.config(), approval_id=approval.approval_id, at=self.at, state=self.state())
        self.assertEqual(reason, "ACTUAL_APPROVAL_REQUIRED")
        self.assertEqual(transport.send_calls, 1)

    def test_global_and_open_order_state_block_before_consumption(self):
        approval = self.approved()
        runtime, store, transport, _ = self.runtime(approval=approval)
        _, reason = runtime.submit_once(config=self.config(), approval_id=approval.approval_id, at=self.at, state=self.state(global_trade_yn="Y"))
        self.assertEqual(reason, "GLOBAL_TRADE_MUST_REMAIN_DISABLED")
        self.assertEqual(store.get(approval.approval_id).status, ApprovalStatus.APPROVED_FOR_ONE_SUBMIT)
        _, reason = runtime.submit_once(config=self.config(), approval_id=approval.approval_id, at=self.at, state=self.state(open_order_count=1))
        self.assertEqual(reason, "ORDER_STATE_BLOCKED")
        self.assertEqual(transport.send_calls, 0)

    def test_concurrent_invocation_consumes_once(self):
        approval = self.approved()
        runtime, store, transport, adapter = self.runtime(approval=approval)
        barrier, results = Barrier(2), []

        def invoke():
            barrier.wait()
            results.append(runtime.submit_once(config=self.config(), approval_id=approval.approval_id, at=self.at, state=self.state())[1])

        threads = [Thread(target=invoke), Thread(target=invoke)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sorted(results), ["ACK", "ACTUAL_APPROVAL_REQUIRED"])
        self.assertEqual(transport.send_calls, 1)
        self.assertEqual(adapter.network_send_calls, 1)

    def test_runtime_module_has_no_kis_or_network_client_import(self):
        with open("src/smoke_send/runtime.py", encoding="utf-8") as file:
            source = file.read().lower()
        self.assertNotIn("kis_client", source)
        self.assertNotIn("requests", source)
        self.assertNotIn("urlopen", source)


if __name__ == "__main__":
    unittest.main()
