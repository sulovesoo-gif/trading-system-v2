import unittest

from src.execution.ownership import ExecutionLane, InMemoryOwnershipLedger, OwnershipKey
from src.forward.lifecycle import ForwardPathCycle, ForwardState

class ForwardLifecycleTest(unittest.TestCase):
 def test_same_product_paths_are_independent_and_deactivate_keeps_exit(self):
    ledger = InMemoryOwnershipLedger()
    path_a = ForwardPathCycle("A", "0197X0")
    path_b = ForwardPathCycle("B", "0197X0")

    for cycle, order_id in ((path_a, "a-buy"), (path_b, "b-buy")):
        cycle.plan_entry()
        cycle.fill(ledger, broker_order_id=order_id, broker_trade_id=order_id, side="BUY", price=100)

    self.assertIs(path_a.state, ForwardState.OPEN)
    self.assertIs(path_b.state, ForwardState.OPEN)
    path_a.active = False
    try:
        path_a.plan_entry()
        self.fail("deactivated OPEN path must not create a new entry")
    except ValueError:
        pass

    path_a.plan_exit()
    path_a.fill(ledger, broker_order_id="a-sell", broker_trade_id="a-sell", side="SELL", price=110)
    self.assertIs(path_a.state, ForwardState.CLOSED)
    self.assertIs(path_b.state, ForwardState.OPEN)
    self.assertEqual(ledger.position(OwnershipKey(ExecutionLane.FORWARD, "A", "0197X0")).quantity, 0)
    self.assertEqual(ledger.position(OwnershipKey(ExecutionLane.FORWARD, "B", "0197X0")).quantity, 1)
