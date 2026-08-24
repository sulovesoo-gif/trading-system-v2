from datetime import datetime
from decimal import Decimal
import unittest

from src.daily_ma_v03.capital import AvailableCash, CapitalEpoch, SettlementAmounts, decide_entry
from src.daily_ma_v03.capital_runtime import DailyMaV04CapitalNoSendRuntime


class _Store:
    def __init__(self):
        self.calls = []

    def plan_entry_no_send(self, **kwargs):
        self.calls.append(kwargs)
        return "REQUEST", "NO_SEND_VALIDATED"


class DailyMaV04CapitalTest(unittest.TestCase):
    def setUp(self):
        self.epoch = CapitalEpoch("DS000103", 1, Decimal("1000000"), Decimal("25000"))

    def test_compound_capital_is_strategy_epoch_local(self):
        self.assertEqual(self.epoch.compound_capital, Decimal("1025000"))
        amounts = SettlementAmounts(Decimal("100000"), Decimal("112000"), buy_fee=Decimal("100"), sell_fee=Decimal("100"), sell_tax=Decimal("200"))
        self.assertEqual(amounts.gross_realized_pnl, Decimal("12000"))
        self.assertEqual(amounts.net_realized_pnl, Decimal("11600"))

    def test_broker_orderable_cash_is_not_double_deducted(self):
        decision = decide_entry(capital=self.epoch,
                                available_cash=AvailableCash(Decimal("1025000"), includes_pending_order_reservations=True),
                                reference_price=Decimal("102500"), locally_reserved_amount=Decimal("900000"))
        self.assertEqual(decision.status, "NO_SEND_VALIDATED")
        self.assertEqual(decision.quantity, 10)
        self.assertEqual(decision.effective_available_cash, Decimal("1025000"))

    def test_local_reservation_only_applies_when_broker_excludes_it(self):
        decision = decide_entry(capital=self.epoch,
                                available_cash=AvailableCash(Decimal("1025000"), includes_pending_order_reservations=False),
                                reference_price=Decimal("102500"), locally_reserved_amount=Decimal("900000"))
        self.assertEqual(decision.status, "INSUFFICIENT_AVAILABLE_CASH")
        self.assertEqual(decision.effective_available_cash, Decimal("125000"))

    def test_insufficient_cash_is_durable_skip_candidate(self):
        decision = decide_entry(capital=self.epoch, available_cash=AvailableCash(Decimal("100"), True), reference_price=Decimal("102500"))
        self.assertEqual(decision.status, "INSUFFICIENT_AVAILABLE_CASH")
        self.assertEqual(decision.quantity, 10)

    def test_runtime_passes_immutable_capital_inputs_to_store(self):
        store = _Store()
        runtime = DailyMaV04CapitalNoSendRuntime(capital_store=store)
        request, status = runtime.plan_entry(
            paper_trade_id=7, strategy_id="DS000103", signal_event_key="event-a", execution_stock_code="0193W0",
            strategy_instance_id="DAILY_MA_DS000103", reference_price=Decimal("100"),
            signal_time=datetime(2026, 8, 24, 15, 18), execution_target_time=datetime(2026, 8, 24, 15, 19),
            capital_epoch_no=1, available_cash=AvailableCash(Decimal("1000000"), True),
            operation_status="LIVE", reconciliation_healthy=True)
        self.assertEqual((request, status), ("REQUEST", "NO_SEND_VALIDATED"))
        self.assertEqual(store.calls[0]["capital_epoch_no"], 1)
        self.assertTrue(store.calls[0]["available_cash"].includes_pending_order_reservations)

    def test_runtime_blocks_without_live_or_reconciliation(self):
        runtime = DailyMaV04CapitalNoSendRuntime(capital_store=_Store())
        base = dict(paper_trade_id=7, strategy_id="DS000103", signal_event_key="event-a", execution_stock_code="0193W0",
                    strategy_instance_id="DAILY_MA_DS000103", reference_price=Decimal("100"),
                    signal_time=datetime(2026, 8, 24, 15, 18), execution_target_time=datetime(2026, 8, 24, 15, 19),
                    capital_epoch_no=1, available_cash=AvailableCash(Decimal("1000000"), True))
        self.assertEqual(runtime.plan_entry(**base, operation_status="PAPER", reconciliation_healthy=True)[1], "OPERATION_NOT_LIVE")
        self.assertEqual(runtime.plan_entry(**base, operation_status="LIVE", reconciliation_healthy=False)[1], "RECONCILIATION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
