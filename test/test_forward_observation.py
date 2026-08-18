import unittest
from datetime import datetime
from decimal import Decimal

from src.forward import ForwardCandidate, ForwardExecutionPath, ForwardPerformanceTracker, ForwardRegistry
from src.execution import ExecutionLane, FillAllocation, InMemoryOwnershipLedger, OwnershipKey


class ForwardObservationTest(unittest.TestCase):
    def candidate(self, candidate_id, exit_identity="EXIT_A"):
        return ForwardCandidate(candidate_id, "research:" + candidate_id, ForwardExecutionPath("ENTRY_A", exit_identity, "0197X0"), "000660", "approved research comparison", datetime(2026, 8, 19), "operator", True)

    def test_same_entry_exit_product_dedups_execution_path_but_keeps_candidate_references(self):
        registry = ForwardRegistry()
        first = registry.register(self.candidate("A")); second = registry.register(self.candidate("B"))
        self.assertEqual(first.path_id, second.path_id)
        self.assertEqual({item.candidate_id for item in registry.path_candidates(first.path_id)}, {"A", "B"})

    def test_different_exit_is_independent_and_book_cap_requires_explicit_value(self):
        registry = ForwardRegistry()
        a = registry.register(self.candidate("A")); b = registry.register(self.candidate("B", "EXIT_B"))
        self.assertNotEqual(a.path_id, b.path_id)
        self.assertEqual(ForwardRegistry.one_share_quantity(), 1)
        self.assertFalse(ForwardRegistry.can_send(configured_book_cap=None, open_acquisition_cost=Decimal("0"), next_cost=Decimal("1")))
        self.assertTrue(ForwardRegistry.can_send(configured_book_cap=Decimal("100"), open_acquisition_cost=Decimal("99"), next_cost=Decimal("1")))

    def test_performance_keeps_one_share_actual_pnl_separate_from_normalized_compound(self):
        tracker = ForwardPerformanceTracker(normalized_initial_capital=Decimal("1000000"))
        result = tracker.record_closed_trade(actual_1share_pnl=Decimal("100"), costs=Decimal("10"), entry_notional=Decimal("1000"), normalized_trade_return=Decimal("0.10"))
        self.assertEqual(result.cost_adjusted_actual_pnl, Decimal("90"))
        self.assertEqual(result.compound_equity, Decimal("1100000.00"))
        self.assertEqual(ForwardRegistry.one_share_quantity(), 1)

    def test_forward_buy_sell_e2e_uses_its_own_execution_path_ownership(self):
        ledger = InMemoryOwnershipLedger()
        owner = OwnershipKey(ExecutionLane.FORWARD, "FORWARD_PATH_A", "0197X0")
        ledger.apply_fill(FillAllocation("order-buy", "trade-buy", owner, "BUY", 1, Decimal("100")))
        self.assertEqual(ledger.position(owner).quantity, 1)
        ledger.apply_fill(FillAllocation("order-sell", "trade-sell", owner, "SELL", 1, Decimal("101")))
        self.assertEqual(ledger.position(owner).quantity, 0)
