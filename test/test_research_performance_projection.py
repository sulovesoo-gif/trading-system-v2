from decimal import Decimal
import unittest

from src.service.research_performance_projection import aggregate, project_cycle


class ResearchPerformanceProjectionTest(unittest.TestCase):
    def cycle(self, code="000660", direction="LONG", legs=None):
        return {"trade_stock_code": code, "direction": direction, "entry_price": Decimal("10000"),
                "exit_price": Decimal("11000"), "holding_seconds": 60, "exit_type": "SIGNAL", "legs": legs or []}

    def test_common_share_uses_ten_million_target(self):
        item = project_cycle(self.cycle(), fee_rate=Decimal("0.001"), sell_tax_rate=Decimal("0.002"))
        self.assertEqual(item["target_capital"], Decimal("10000000"))
        self.assertEqual(item["quantity"], 1000)
        self.assertEqual(item["gross_realized_profit"] - item["total_trading_cost"], item["realized_profit"])

    def test_etf_uses_one_million_target(self):
        item = project_cycle(self.cycle("0193T0"), fee_rate=Decimal("0.001"), sell_tax_rate=Decimal("0"))
        self.assertEqual(item["target_capital"], Decimal("1000000"))
        self.assertEqual(item["quantity"], 100)

    def test_accumulated_uses_persisted_leg_ratios(self):
        legs = [{"entry_price": Decimal("10000"), "entry_ratio": Decimal("0.333333333333333333")},
                {"entry_price": Decimal("11000"), "entry_ratio": Decimal("0.333333333333333333")}]
        item = project_cycle(self.cycle("0197X0", legs=legs), fee_rate=Decimal("0"), sell_tax_rate=Decimal("0"))
        self.assertEqual(item["quantity"], 63)
        self.assertLess(item["invested_amount"], Decimal("1000000"))

    def test_aggregate_uses_net_invested_return(self):
        item = project_cycle(self.cycle("0193T0"), fee_rate=Decimal("0"), sell_tax_rate=Decimal("0"))
        result = aggregate([item])
        self.assertEqual(result["invested_return_rate"], item["invested_return_rate"])
