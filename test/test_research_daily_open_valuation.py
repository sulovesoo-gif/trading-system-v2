from datetime import date
from decimal import Decimal
import unittest

from scripts.dashboard.serve_multi_ma_dashboard import _apply_daily_open_valuation


class DailyOpenValuationTest(unittest.TestCase):
  def test_uses_single_run_initial_capital_without_double_counting(self):
    ranking = [{
        "trade_stock_code": "000660", "signal_source_stock_code": "000660",
        "strategy_code": "SIGNAL_1", "observation_code": "COMPLETE", "direction": "LONG",
        "realized_profit": Decimal("123000"),
    }]
    daily = [{
        "trading_date": date(2026, 8, 3), "trade_stock_code": "000660", "signal_source_stock_code": "000660",
        "strategy_code": "SIGNAL_1", "observation_code": "COMPLETE", "direction": "LONG",
        "realized_profit": Decimal("123000"),
    }]
    positions = [{
        "trading_date": date(2026, 8, 3), "trade_stock_code": "000660", "signal_source_stock_code": "000660",
        "strategy_code": "SIGNAL_1", "observation_code": "COMPLETE", "direction": "LONG",
        "unrealized_profit": Decimal("45000"), "is_latest": True,
    }]

    ranked, days, overview = _apply_daily_open_valuation(ranking, daily, positions, Decimal("10000000"))

    self.assertEqual(ranked[0]["open_valuation_profit"], Decimal("45000"))
    self.assertEqual(ranked[0]["total_valuation_profit"], Decimal("168000"))
    self.assertEqual(ranked[0]["realized_capital_return_rate"], Decimal("1.2300"))
    self.assertEqual(ranked[0]["total_valuation_return_rate"], Decimal("1.6800"))
    self.assertEqual(days[0]["total_valuation_profit"], Decimal("168000"))
    self.assertEqual(overview["combination_count"], 1)


  def test_without_open_equals_realized_return(self):
    ranking = [{
        "trade_stock_code": "000660", "signal_source_stock_code": "000660",
        "strategy_code": "SIGNAL_1", "observation_code": "COMPLETE", "direction": "LONG",
        "realized_profit": Decimal("10000"),
    }]
    ranked, _, _ = _apply_daily_open_valuation(ranking, [], [], Decimal("10000000"))
    self.assertEqual(ranked[0]["open_valuation_profit"], Decimal("0"))
    self.assertEqual(ranked[0]["realized_capital_return_rate"], ranked[0]["total_valuation_return_rate"])

  def test_position_daily_values_are_shown_for_each_valuation_date(self):
    key = {
        "trade_stock_code": "0193T0", "signal_source_stock_code": "000660",
        "strategy_code": "SIGNAL_2", "observation_code": "COMPLETE", "direction": "LONG",
    }
    positions = [
        {**key, "trading_date": date(2026, 8, 3), "unrealized_profit": Decimal("1000"), "is_latest": False},
        {**key, "trading_date": date(2026, 8, 4), "unrealized_profit": Decimal("2500"), "is_latest": True},
    ]
    _, days, _ = _apply_daily_open_valuation([], [], positions, Decimal("10000000"))
    self.assertEqual([(row["trading_date"], row["open_valuation_profit"]) for row in days], [
        (date(2026, 8, 4), Decimal("2500")), (date(2026, 8, 3), Decimal("1000")),
    ])
