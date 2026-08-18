import unittest
from decimal import Decimal

from src.execution import ExecutionLane, FillAllocation, InMemoryOwnershipLedger, OwnershipError, OwnershipKey


class OwnershipTest(unittest.TestCase):
    def fill(self, owner, trade, side, qty, price):
        return FillAllocation("order-" + trade, trade, owner, side, qty, Decimal(str(price)))

    def test_same_product_owners_remain_isolated_and_sell_cannot_cross(self):
        ledger = InMemoryOwnershipLedger()
        three = OwnershipKey(ExecutionLane.LIVE, "LIVE_STRATEGY_3", "0197X0")
        five = OwnershipKey(ExecutionLane.LIVE, "LIVE_STRATEGY_4", "0197X0")
        ledger.apply_fill(self.fill(three, "a", "BUY", 10, 100))
        ledger.apply_fill(self.fill(five, "b", "BUY", 20, 100))
        ledger.apply_fill(self.fill(three, "c", "SELL", 10, 110))
        self.assertEqual((ledger.position(three).quantity, ledger.position(five).quantity), (0, 20))
        with self.assertRaises(OwnershipError): ledger.apply_fill(self.fill(three, "d", "SELL", 1, 110))

    def test_duplicate_fill_and_reconciliation_unattributed_are_deterministic(self):
        ledger = InMemoryOwnershipLedger()
        live = OwnershipKey(ExecutionLane.LIVE, "LIVE_STRATEGY_4", "0197X0")
        forward = OwnershipKey(ExecutionLane.FORWARD, "FORWARD_A", "0197X0")
        fill = self.fill(live, "a", "BUY", 20, 100)
        ledger.apply_fill(fill); ledger.apply_fill(fill)
        ledger.apply_fill(self.fill(forward, "b", "BUY", 1, 100))
        self.assertEqual(ledger.position(live).quantity, 20)
        result = ledger.reconcile({"0197X0": 22})[0]
        self.assertEqual((result.attributed_quantity, result.unattributed_quantity, result.status), (21, 1, "UNATTRIBUTED"))
