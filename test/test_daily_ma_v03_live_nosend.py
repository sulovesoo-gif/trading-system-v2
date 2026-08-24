from datetime import datetime
from decimal import Decimal
import unittest

from src.daily_ma_v03.live_nosend import InMemoryDailyMaLiveNoSendStore, NoSendIntent, entry_intent_key


class DailyMaV03LiveNoSendTest(unittest.TestCase):
    def test_entry_is_idempotent_without_broker_reference_or_live_trade(self):
        key = entry_intent_key(strategy_id="802", signal_event_key="event")
        intent = NoSendIntent(key, 123, "802", "event", "ENTRY", "BUY", 3, Decimal("100"), datetime(2026, 8, 24, 15, 18))
        store = InMemoryDailyMaLiveNoSendStore()
        request, created = store.prepare(intent=intent, execution_stock_code="0193W0", execution_target_time=datetime(2026, 8, 24, 15, 19), global_trade_yn="N")
        duplicate, duplicate_created = store.prepare(intent=intent, execution_stock_code="0193W0", execution_target_time=datetime(2026, 8, 24, 15, 19), global_trade_yn="N")
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(request, duplicate)
        self.assertIsNone(request.broker_order_id)
        self.assertIsNone(intent.live_trade_id)
        self.assertEqual(store.reservations[key].remaining_reserved_amount, Decimal("300"))

    def test_global_trade_must_remain_off(self):
        intent = NoSendIntent("key", 1, "1", "event", "ENTRY", "BUY", 1, Decimal("10"), datetime(2026, 8, 24, 15, 18))
        with self.assertRaisesRegex(ValueError, "GLOBAL_TRADE_YN=N"):
            InMemoryDailyMaLiveNoSendStore().prepare(intent=intent, execution_stock_code="X", execution_target_time=datetime(2026, 8, 24, 15, 19), global_trade_yn="Y")
