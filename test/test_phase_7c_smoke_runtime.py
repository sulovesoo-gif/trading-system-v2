import unittest
from datetime import date, datetime, time
from threading import Barrier, Thread
from unittest.mock import patch

from src.broker import BrokerMode, KisBrokerAdapter
from src.smoke_gate import SMOKE_OWNERSHIP_ID, ResolvedSmokeConfig
from src.smoke_send import (
    ActualApproval,
    ApprovalStatus,
    DeterministicTransport,
    InMemorySmokeApprovalStore,
    Phase7CSmokeRuntime,
    SmokeGateState,
)
from src.smoke_send.authorization import _context_from_consumed_approval


class Phase7CSmokeRuntimeTest(unittest.TestCase):
    at = datetime(2026, 8, 19, 10, 5)

    def config(self, **changes):
        values = dict(
            active_stock_code="0193W0", strategy_instance_id=SMOKE_OWNERSHIP_ID,
            allowed_date=self.at.date(), allowed_time_from=time(10), allowed_time_to=time(10, 10),
        )
        values.update(changes)
        return ResolvedSmokeConfig(**values)

    def approved(self, status=ApprovalStatus.APPROVED_FOR_ONE_SUBMIT):
        return ActualApproval("00000000-0000-0000-0000-000000000007", SMOKE_OWNERSHIP_ID, "0193W0", self.at.date(), time(10), time(10, 10), status)

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

    def test_explicit_reject_is_consumed_and_never_creates_a_fill(self):
        approval = self.approved()
        runtime, store, transport, _ = self.runtime("REJECT", approval)
        response, reason = runtime.submit_once(config=self.config(), approval_id=approval.approval_id, at=self.at, state=self.state())
        self.assertEqual(reason, "REJECTED")
        self.assertEqual(response["rt_cd"], "1")
        self.assertEqual(store.get(approval.approval_id).status, ApprovalStatus.CONSUMED)
        self.assertEqual(store.get(approval.approval_id).broker_state, "ACK_REJECTED")
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

    def test_smoke_sell_requires_its_own_approval_and_one_smoke_share(self):
        approval = ActualApproval(
            "00000000-0000-0000-0000-000000000009", SMOKE_OWNERSHIP_ID, "0193W0",
            self.at.date(), time(10), time(10, 10), ApprovalStatus.APPROVED_FOR_ONE_SUBMIT,
            side="SELL",
        )
        runtime, store, transport, _ = self.runtime(approval=approval)
        sell_config = self.config(phase="7C-2", side="SELL")
        _, reason = runtime.submit_once(config=sell_config, approval_id=approval.approval_id, at=self.at, state=self.state())
        self.assertEqual(reason, "SMOKE_LOGICAL_POSITION_REQUIRED")
        self.assertEqual(store.get(approval.approval_id).status, ApprovalStatus.APPROVED_FOR_ONE_SUBMIT)
        _, reason = runtime.submit_once(config=sell_config, approval_id=approval.approval_id, at=self.at, state=self.state(smoke_logical_position_quantity=1))
        self.assertEqual(reason, "ACK")
        self.assertEqual(transport.send_calls, 1)

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

    def test_direct_phase_adapter_requires_runtime_context_and_context_is_one_shot(self):
        approval = self.approved(ApprovalStatus.CONSUMED)
        _, _, transport, adapter = self.runtime()
        order = Phase7CSmokeRuntime._broker_order(approval)
        with self.assertRaises(RuntimeError):
            adapter.submit(order)
        with self.assertRaises(RuntimeError):
            adapter.submit(order, authorized_context="approval-id-only")
        context = _context_from_consumed_approval(approval)
        adapter.submit(order, authorized_context=context)
        self.assertEqual(transport.send_calls, 1)
        with self.assertRaises(RuntimeError):
            adapter.submit(order, authorized_context=context)
        self.assertEqual(transport.send_calls, 1)

    def test_context_is_issued_only_after_successful_cas(self):
        runtime, _, transport, _ = self.runtime(approval=self.approved(ApprovalStatus.NOT_APPROVED))
        with patch("src.smoke_send.runtime._context_from_consumed_approval") as factory:
            _, reason = runtime.submit_once(config=self.config(), approval_id=self.approved().approval_id, at=self.at, state=self.state())
        self.assertEqual(reason, "ACTUAL_APPROVAL_REQUIRED")
        factory.assert_not_called()
        self.assertEqual(transport.send_calls, 0)

        approval = self.approved()
        runtime, _, transport, _ = self.runtime(approval=approval)
        with patch("src.smoke_send.runtime._context_from_consumed_approval", wraps=_context_from_consumed_approval) as factory:
            _, reason = runtime.submit_once(config=self.config(), approval_id=approval.approval_id, at=self.at, state=self.state())
        self.assertEqual(reason, "ACK")
        factory.assert_called_once()
        self.assertEqual(transport.send_calls, 1)

    def test_approval_order_scope_cannot_be_overridden_by_runtime_config(self):
        approval = self.approved()
        runtime, store, transport, adapter = self.runtime(approval=approval)
        for config in (
            self.config(side="SELL"), self.config(quantity=2),
            self.config(exchange="NXT"), self.config(exchange="SOR"),
            self.config(order_division="01"), self.config(order_division="00"),
            self.config(order_price="1000"),
        ):
            _, reason = runtime.submit_once(config=config, approval_id=approval.approval_id, at=self.at, state=self.state())
            self.assertEqual(reason, "RESOLVED_CONFIG_INVALID")
            self.assertEqual(store.get(approval.approval_id).status, ApprovalStatus.APPROVED_FOR_ONE_SUBMIT)
        self.assertEqual(transport.send_calls, 0)
        self.assertEqual(adapter.network_send_calls, 0)

    def test_approval_side_or_quantity_tampering_blocks_before_cas(self):
        for changes in (
            {"side": "SELL"}, {"quantity": 2}, {"exchange": "NXT"},
            {"exchange": "SOR"}, {"order_division": "01"},
            {"order_division": "00"}, {"order_price": "1000"},
        ):
            approval = ActualApproval(
                "00000000-0000-0000-0000-000000000007", SMOKE_OWNERSHIP_ID, "0193W0",
                self.at.date(), time(10), time(10, 10), ApprovalStatus.APPROVED_FOR_ONE_SUBMIT,
                side=changes.get("side", "BUY"), quantity=changes.get("quantity", 1),
                exchange=changes.get("exchange", "KRX"),
                order_division=changes.get("order_division", "15"),
                order_price=changes.get("order_price", "0"),
            )
            runtime, store, transport, adapter = self.runtime(approval=approval)
            _, reason = runtime.submit_once(config=self.config(), approval_id=approval.approval_id, at=self.at, state=self.state())
            self.assertEqual(reason, "APPROVAL_CONFIG_MISMATCH")
            self.assertEqual(store.get(approval.approval_id).status, ApprovalStatus.APPROVED_FOR_ONE_SUBMIT)
            self.assertEqual(transport.send_calls, 0)
            self.assertEqual(adapter.network_send_calls, 0)

    def test_approval_creation_validates_scope_and_deterministic_key(self):
        store = InMemorySmokeApprovalStore()
        valid = self.approved()
        store.create_approved(approval=valid, config=self.config())
        self.assertEqual(store.get(valid.approval_id), valid)
        invalid = ActualApproval(
            "00000000-0000-0000-0000-000000000008", SMOKE_OWNERSHIP_ID, "0193W0",
            self.at.date(), time(10), time(10, 10), ApprovalStatus.APPROVED_FOR_ONE_SUBMIT,
            broker_idempotency_key="not-derived",
        )
        with self.assertRaises(ValueError):
            store.create_approved(approval=invalid, config=self.config())

    def test_cas_scope_mismatch_cannot_consume_or_issue_context(self):
        approval = self.approved()
        store = InMemorySmokeApprovalStore()
        store.save(approval)
        self.assertIsNone(store.consume_immediately_before_send(
            approval.approval_id, approval.broker_idempotency_key,
            stock_code="0193W0", strategy_instance_id=SMOKE_OWNERSHIP_ID,
            side="SELL", quantity=1, exchange="KRX", order_division="15", order_price="0",
        ))
        self.assertIsNone(store.consume_immediately_before_send(
            approval.approval_id, approval.broker_idempotency_key,
            stock_code="0193W0", strategy_instance_id=SMOKE_OWNERSHIP_ID,
            side="BUY", quantity=2, exchange="KRX", order_division="15", order_price="0",
        ))
        for changes in (
            {"stock_code": "0197X0"}, {"strategy_instance_id": "LIVE_STRATEGY_3"},
            {"exchange": "NXT"}, {"order_division": "01"}, {"order_price": "1000"},
        ):
            values = dict(stock_code="0193W0", strategy_instance_id=SMOKE_OWNERSHIP_ID,
                          side="BUY", quantity=1, exchange="KRX",
                          order_division="15", order_price="0")
            values.update(changes)
            self.assertIsNone(store.consume_immediately_before_send(
                approval.approval_id, approval.broker_idempotency_key, **values,
            ))
        self.assertEqual(store.get(approval.approval_id).status, ApprovalStatus.APPROVED_FOR_ONE_SUBMIT)

    def test_runtime_module_has_no_kis_or_network_client_import(self):
        with open("src/smoke_send/runtime.py", encoding="utf-8") as file:
            source = file.read().lower()
        self.assertNotIn("kis_client", source)
        self.assertNotIn("requests", source)
        self.assertNotIn("urlopen", source)


if __name__ == "__main__":
    unittest.main()
