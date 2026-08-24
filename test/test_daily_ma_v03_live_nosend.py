from datetime import datetime
from decimal import Decimal
import unittest
from pathlib import Path

from src.daily_ma_v03.live_nosend import DailyMaLiveNoSendRuntime, InMemoryDailyMaLiveNoSendStore, NoSendIntent, entry_intent_key


class DailyMaV03LiveNoSendTest(unittest.TestCase):
    def test_entry_is_idempotent_without_broker_reference_or_live_trade(self):
        key = entry_intent_key(strategy_id="802", signal_event_key="event")
        intent = NoSendIntent(key, 123, "802", "event", "ENTRY", "BUY", 3, Decimal("100"), datetime(2026, 8, 24, 15, 18))
        store = InMemoryDailyMaLiveNoSendStore()
        request, created = store.prepare(intent=intent, execution_stock_code="0193W0", strategy_instance_id="DAILY_MA_802", execution_target_time=datetime(2026, 8, 24, 15, 19), global_trade_yn="N")
        duplicate, duplicate_created = store.prepare(intent=intent, execution_stock_code="0193W0", strategy_instance_id="DAILY_MA_802", execution_target_time=datetime(2026, 8, 24, 15, 19), global_trade_yn="N")
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(request, duplicate)
        self.assertIsNone(request.broker_order_id)
        self.assertIsNone(intent.live_trade_id)
        self.assertEqual(store.reservations[key].remaining_reserved_amount, Decimal("300"))

    def test_global_trade_must_remain_off(self):
        intent = NoSendIntent("key", 1, "1", "event", "ENTRY", "BUY", 1, Decimal("10"), datetime(2026, 8, 24, 15, 18))
        with self.assertRaisesRegex(ValueError, "GLOBAL_TRADE_YN=N"):
            InMemoryDailyMaLiveNoSendStore().prepare(intent=intent, execution_stock_code="X", strategy_instance_id="DAILY_MA_1", execution_target_time=datetime(2026, 8, 24, 15, 19), global_trade_yn="Y")

    def test_runtime_blocks_nonlive_and_reconciliation_then_plans_once(self):
        runtime = DailyMaLiveNoSendRuntime(store=InMemoryDailyMaLiveNoSendStore())
        args = dict(paper_trade_id=1, strategy_id="802", signal_event_key="event", execution_stock_code="0193W0", strategy_instance_id="DAILY_MA_802", quantity=1, reference_price=Decimal("100"), signal_time=datetime(2026,8,24,15,18), execution_target_time=datetime(2026,8,24,15,19))
        self.assertEqual(runtime.plan_entry(**args, operation_status="PAPER", reconciliation_healthy=True)[1], "OPERATION_NOT_LIVE")
        self.assertEqual(runtime.plan_entry(**args, operation_status="LIVE", reconciliation_healthy=False)[1], "RECONCILIATION_REQUIRED")
        first = runtime.plan_entry(**args, operation_status="LIVE", reconciliation_healthy=True)
        second = runtime.plan_entry(**args, operation_status="LIVE", reconciliation_healthy=True)
        self.assertEqual(first[1], "NO_SEND_VALIDATED")
        self.assertEqual(first[0], second[0])

    def test_exit_is_ownership_scoped_and_normal_is_suppressed_after_day20_close(self):
        runtime = DailyMaLiveNoSendRuntime(store=InMemoryDailyMaLiveNoSendStore())
        args = dict(paper_trade_id=9, strategy_id="802", signal_event_key="event", execution_stock_code="0193W0", strategy_instance_id="DAILY_MA_802", quantity=1, reference_price=Decimal("100"), source_event_time=datetime(2026,8,24,10,0))
        self.assertEqual(runtime.plan_exit(**args, exit_reason="DAY20_EXIT", ownership_remaining=0)[1], "OWNERSHIP_REQUIRED")
        first = runtime.plan_exit(**args, exit_reason="DAY20_EXIT", ownership_remaining=1)
        duplicate = runtime.plan_exit(**args, exit_reason="DAY20_EXIT", ownership_remaining=1)
        self.assertEqual(first[0], duplicate[0])
        self.assertEqual(runtime.plan_exit(**args, exit_reason="NORMAL_EXIT", ownership_remaining=1, live_actual_closed=True)[1], "LIVE_ALREADY_CLOSED")

    def test_postgres_store_has_no_broker_transport_dependency(self):
        content = Path("src/daily_ma_v03/live_nosend_repository.py").read_text(encoding="utf-8")
        self.assertIn("NO_SEND_VALIDATED", content)
        self.assertNotIn("KisBrokerAdapter", content)
        self.assertNotIn("KISOrderPostTransport", content)
