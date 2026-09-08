import unittest
from datetime import datetime, timedelta
from decimal import Decimal as D

from src.flow_v3.accounting import AccountingError, ROUND_TRIP, calculate, quantity

T = datetime(2026, 9, 1, 9)


def trade(i, start=0, end=None, entry='100', exit='200'):
    return dict(paper_trade_id=i, trade_status='OPEN' if end is None else 'CLOSED',
                entry_execution_time=T+timedelta(minutes=start), entry_execution_price=D(entry),
                actual_exit_time=None if end is None else T+timedelta(minutes=end),
                actual_exit_price=None if end is None else D(exit))


class AccountingTest(unittest.TestCase):
    def test_cost_contract_matches_research_sql(self):
        lot = calculate([trade(1,end=1,entry='7935',exit='7985')],previous_close='9545')[0][1]
        self.assertEqual(lot['net_return_pct'].quantize(D('.00000001')), D('.56081432'))

    def test_quantity_grows_beyond_one(self):
        lots,_,summary=calculate([trade(1,end=1),trade(2,start=1,end=2),trade(3,start=2)], previous_close='100')
        self.assertEqual([lots[i]['quantity'] for i in (1,2,3)],[1,2,4])
        self.assertEqual(summary['maximum_quantity'],4)

    def test_exit_precedes_same_timestamp_entry(self):
        lots,_,_=calculate([trade(2,start=1),trade(1,end=1)],previous_close='100')
        self.assertEqual(lots[2]['quantity'],2)

    def test_open_lot_does_not_block_new_entry(self):
        lots,_,summary=calculate([trade(1),trade(2,start=1)],previous_close='100')
        self.assertEqual(len(lots),2)
        self.assertEqual(summary['current_capital'],D('150'))
        self.assertEqual(summary['open_count'],2)

    def test_realized_only_overnight(self):
        _,days,summary=calculate([trade(1,end=1500)],previous_close='100')
        self.assertEqual(days[T.date()]['closed_count'],0)
        self.assertEqual(days[(T+timedelta(minutes=1500)).date()]['closed_count'],1)
        self.assertGreater(summary['current_capital'],D('150'))

    def test_restart_rebuild_is_identical(self):
        ts=[trade(1,end=2),trade(2,start=1,end=3)]
        self.assertEqual(calculate(ts,previous_close='100'),calculate(list(reversed(ts)),previous_close='100'))

    def test_late_trade_rebuild_converges(self):
        ts=[trade(1,end=1),trade(2,start=2,end=3)]
        calculate(ts[1:],previous_close='100')
        self.assertEqual(calculate(ts,previous_close='100'),calculate(ts[::-1],previous_close='100'))

    def test_duplicate_is_not_settled_twice(self):
        with self.assertRaisesRegex(AccountingError,'DUPLICATE'):
            calculate([trade(1),trade(1)],previous_close='100')

    def test_missing_initial_price_fails_closed(self):
        with self.assertRaises(AccountingError):
            calculate([trade(1)],previous_close=None)

    def test_invalid_exit_fails_closed(self):
        with self.assertRaisesRegex(AccountingError,'INVALID_EXIT_TIME'):
            calculate([trade(1,start=1,end=0)],previous_close='100')

    def test_same_time_self_exit_charges_full_cost(self):
        t=trade(230266,end=0,entry='10000',exit='10000')
        t['entry_signal_time']=T-timedelta(minutes=2)
        t['normal_exit_signal_time']=T-timedelta(minutes=1)
        lots,days,s=calculate([t],previous_close='10000')
        self.assertEqual(lots[230266]['quantity'],1)
        self.assertEqual(lots[230266]['net_pnl'],-D('10000')*ROUND_TRIP)
        self.assertEqual(s['open_count'],0)
        self.assertEqual(days[T.date()]['closed_count'],1)

    def test_abc_phase_order(self):
        ts=[trade(1,end=1),trade(2,start=1,end=1),trade(3,start=1)]
        lots,_,_=calculate(ts,previous_close='100')
        self.assertEqual(lots[2]['quantity'],2)
        self.assertEqual(lots[3]['quantity'],2)
        self.assertEqual(lots[2]['capital_at_entry'],lots[3]['capital_at_entry'])
        self.assertEqual(calculate(ts,previous_close='100'),calculate(ts[::-1],previous_close='100'))

    def test_zero_duration_inverted_signals_rejected(self):
        t=trade(1,end=0)
        t['entry_signal_time']=T-timedelta(minutes=1)
        t['normal_exit_signal_time']=T-timedelta(minutes=2)
        with self.assertRaisesRegex(AccountingError,'INVALID_SIGNAL_ORDER'):
            calculate([t],previous_close='100')

    def test_zero_quantity_keeps_observation(self):
        lots,_,s=calculate([trade(1,end=1,entry='1000')],previous_close='100')
        self.assertEqual(lots[1]['quantity'],0)
        self.assertEqual(s['trade_count'],1)
        self.assertEqual(s['net_pnl'],0)

    def test_negative_capital_cannot_borrow(self):
        self.assertEqual(quantity('-1','100'),0)

    def test_nonfinite_price_rejected(self):
        for p in ('NaN','Infinity','0','-1'):
            with self.assertRaises(AccountingError): quantity('100',p)

    def test_cancelled_observation_not_realized(self):
        t=trade(1,end=1);t['trade_status']='CANCELLED'
        self.assertEqual(calculate([t],previous_close='100')[2]['trade_count'],0)


if __name__=='__main__': unittest.main()
