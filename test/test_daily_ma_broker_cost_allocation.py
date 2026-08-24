from datetime import date, datetime
from decimal import Decimal
import unittest

from src.daily_ma_v03.broker_cost_allocation import (
    BrokerCostSnapshot, BrokerCostStatus, BrokerCostTotals, CostAllocationTarget,
    allocate_final_costs, classify_snapshot,
)
from src.daily_ma_v03.broker_cost_settlement import settlement_amounts


class BrokerCostAllocationTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = BrokerCostSnapshot(
            date(2026, 8, 25), "005930", BrokerCostTotals(Decimal("5"), Decimal("7"), Decimal("3")),
            datetime(2026, 8, 25, 18), True, BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK,
        )
        self.targets = (
            CostAllocationTarget(11, "BUY", Decimal("100"), "A|BUY"),
            CostAllocationTarget(12, "BUY", Decimal("200"), "B|BUY"),
            CostAllocationTarget(11, "SELL", Decimal("100"), "A|SELL"),
            CostAllocationTarget(12, "SELL", Decimal("200"), "B|SELL"),
        )

    def test_partial_fill_product_day_costs_are_exact_and_deterministic(self):
        status, rows = allocate_final_costs(snapshot=self.snapshot, targets=self.targets)
        self.assertEqual(status, BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK)
        self.assertEqual(sum(row.buy_fee for row in rows), Decimal("5"))
        self.assertEqual(sum(row.sell_fee for row in rows), Decimal("7"))
        self.assertEqual(sum(row.sell_tax for row in rows), Decimal("3"))
        self.assertEqual(rows, allocate_final_costs(snapshot=self.snapshot, targets=self.targets)[1])

    def test_unattributed_activity_blocks_without_allocating(self):
        status, rows = allocate_final_costs(snapshot=self.snapshot, targets=self.targets, unattributed_activity=True)
        self.assertEqual(status, BrokerCostStatus.BROKER_COST_ATTRIBUTION_BLOCKED)
        self.assertEqual(rows, ())

    def test_final_costs_build_net_settlement_only_when_both_sides_exist(self):
        _, rows = allocate_final_costs(snapshot=self.snapshot, targets=self.targets)
        amounts = settlement_amounts(live_trade_id=11, entry_filled_amount=Decimal('100'),
                                    exit_filled_amount=Decimal('110'), allocations=rows)
        self.assertEqual(amounts.net_realized_pnl, Decimal('4'))

    def test_pending_snapshot_does_not_allocate(self):
        pending = BrokerCostSnapshot(self.snapshot.trade_date, self.snapshot.execution_stock_code,
                                     self.snapshot.totals, self.snapshot.broker_snapshot_at, False,
                                     BrokerCostStatus.PENDING_BROKER_COST)
        self.assertEqual(allocate_final_costs(snapshot=pending, targets=self.targets)[0], BrokerCostStatus.PENDING_BROKER_COST)

    def test_final_snapshot_regression_is_fail_closed(self):
        observed = BrokerCostSnapshot(self.snapshot.trade_date, self.snapshot.execution_stock_code,
                                      BrokerCostTotals(Decimal("4"), Decimal("7"), Decimal("3")),
                                      self.snapshot.broker_snapshot_at, True, BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK)
        self.assertEqual(classify_snapshot(stored=self.snapshot, observed=observed), BrokerCostStatus.BROKER_COST_SNAPSHOT_REGRESSION)


if __name__ == "__main__":
    unittest.main()
