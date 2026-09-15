from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal as D
import subprocess
import unittest
from unittest.mock import patch

from src.flow_v3_leadership.research import stock_costs, replay, VERSION, COST_CONTRACT
from src.service.research_backfill_service import ResearchCostPolicy
from src.service.research_complete_replay_service import CompleteReplay, Position, ResearchLeg, ResearchSignal
from scripts.research.backfill_flow_v3_leadership import execute_plan
from test.test_flow_v3_leadership import trade, at, DAY, strategy, state, Prices, Session, extended_trades


class StockCostTest(unittest.TestCase):
    def test_exact_cost_breakdown(self):
        self.assertEqual(stock_costs(1, 100, 110), dict(
            gross=D(10), buy_fee=D('0.014052700'), sell_fee=D('0.015457970'),
            sell_tax=D('0.220'), net=D('9.750489330')))

    def test_tax_sell_only_quantity_scaled(self):
        a = stock_costs(3, 100, 110)
        b = stock_costs(3, 50, 110)
        self.assertEqual(a['sell_tax'], D('0.660'))
        self.assertEqual(a['sell_tax'], b['sell_tax'])
        self.assertNotEqual(a['buy_fee'], b['buy_fee'])

    def test_policy_existing_etf_slippage_unchanged(self):
        p = ResearchCostPolicy()
        self.assertEqual(p.for_stock('000660'), (D('0.000140527'), D('0.002')))
        self.assertEqual(p.for_stock('0193T0'), (D('0.000146527'), D(0)))
        self.assertEqual(p.for_stock('0197X0'), (D('0.000146527'), D(0)))
        self.assertEqual(p.slippage_rate, 0)

    def test_policy_flows_into_existing_close_calculator(self):
        fee, tax = ResearchCostPolicy().for_stock('000660')
        calculator = CompleteReplay(fee_rate=fee, sell_tax_rate=tax)
        signal = ResearchSignal(at(10, 0), 'SIGNAL_1', 'LONG', None)
        leg = ResearchLeg('SIGNAL_1', at(10, 0), at(10, 1), D(100), D(1), 10, D(1000))
        result = calculator._close('SIGNAL_1', Position('LONG', signal, at(10, 1), [leg]),
            signal_time=at(11, 0), exit_time=at(11, 1), exit_price=D(110), exit_type='NORMAL')
        self.assertEqual(result.sell_tax, D('2.20'))
        self.assertEqual(result.realized_profit,
                         result.gross_realized_profit-result.buy_fee-result.sell_fee-result.sell_tax)

    def test_tax_changes_next_quantity_not_only_report(self):
        trades = [trade(1, at(10, 1), at(10, 2), 100, 200),
                  trade(2, at(10, 3), at(10, 4), D('199.7'), 210)]
        new = replay(trades, 100, DAY)['daily']
        with patch('src.flow_v3_leadership.research.STOCK_SELL_TAX', D(0)):
            old = replay(trades, 100, DAY)['daily']
        self.assertEqual((new['trade_count'], new['skipped_quantity_count']), (1, 1))
        self.assertEqual((old['trade_count'], old['skipped_quantity_count']), (2, 0))
        self.assertLess(new['final_capital'], old['final_capital'])
        self.assertLess(new['compound_return'], old['compound_return'])

    def test_same_signals_prices_lifecycle_unchanged(self):
        states = [state(at(16, 1)), state(at(16, 3), -1)]
        prices = Prices([(at(16, 2), 100), (at(16, 4), 110)])
        before, _ = extended_trades(strategy(), states, prices, 'AFTER', Session(), DAY)
        original = deepcopy(before)
        new = replay(before, 6000000, DAY)
        with patch('src.flow_v3_leadership.research.STOCK_SELL_TAX', D(0)):
            old_trades, _ = extended_trades(strategy(), states, prices, 'AFTER', Session(), DAY)
            old = replay(old_trades, 6000000, DAY)
        self.assertEqual(original, before)
        self.assertEqual(original, old_trades)
        self.assertLess(new['daily']['final_capital'], old['daily']['final_capital'])

    def test_mdd_includes_tax(self):
        trades = [trade(1, at(10, 1), at(11, 1), 100, 100)]
        new = replay(trades, 100, DAY)['daily']
        with patch('src.flow_v3_leadership.research.STOCK_SELL_TAX', D(0)):
            old = replay(trades, 100, DAY)['daily']
        self.assertGreater(new['mdd'], old['mdd'])

    def test_new_cost_version_identifiable(self):
        self.assertNotEqual(VERSION, 'LEADERSHIP_V1_STOCK_COST_09A08')
        self.assertIn('0.002 sell-side', COST_CONTRACT)


class BackfillLoopTest(unittest.TestCase):
    def test_existing_runner_same_start_real_dates_only(self):
        first = date(2026, 8, 31)
        dates = [first, date(2026, 9, 4), date(2026, 9, 7)]
        calls = []
        execute_plan(dict(research_start=first, dates=dates, blockers=[]), write=True,
                     runner=lambda command, **kwargs: calls.append((command, kwargs)))
        self.assertEqual(len(calls), 3)
        for (command, kwargs), day in zip(calls, dates):
            self.assertIn('run_flow_v3_leadership.py', command[1])
            self.assertEqual(command[2:], ['--date', str(day), '--research-start', str(first), '--write-snapshot'])
            self.assertTrue(kwargs['check'])

    def test_missing_schema_or_old_version_stops_before_run(self):
        def unexpected(*args, **kwargs): self.fail('runner should not be called')
        for reason in ('LEADERSHIP_SCHEMA_NOT_INSTALLED', 'IMMUTABLE_OLD_COST_VERSION_CONFLICT'):
            with self.assertRaises(ValueError):
                execute_plan(dict(blockers=[reason]), write=True, runner=unexpected)

    def test_first_failed_day_stops_later_dates(self):
        calls = []
        def fail(command, **kwargs):
            calls.append(command)
            raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError):
            execute_plan(dict(research_start=DAY, dates=[DAY, DAY+timedelta(days=1)], blockers=[]), runner=fail)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__': unittest.main()
