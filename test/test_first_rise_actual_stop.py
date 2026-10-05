"""Actual protection: fake REST/POST; database cases use session TEMP only."""
import unittest
from decimal import Decimal as D
from types import SimpleNamespace
from unittest.mock import Mock,patch

from src.first_rise_breakout.j_protection import ActualStopProtection
from test.test_first_rise_j_wiring_e2e import WiringE2E,at
from test import test_first_rise_j_exit_continuity as continuity


class ProtectionPollTests(unittest.TestCase):
    def worker(self,stocks):
        self.planner=Mock()
        self.planner.protection_stocks.return_value=stocks
        self.planner.plan_exits.return_value=0
        self.planner.protection_request_keys.return_value=[]
        self.lookup=Mock();self.lookup.current_price.return_value=D(100)
        return ActualStopProtection(planner=self.planner,price_lookup=self.lookup,
            submitter=Mock(),clock=lambda:at(9,15),session_open=lambda _:True)

    def test_no_open_no_rest(self):
        self.worker([]).poll();self.lookup.current_price.assert_not_called()

    def test_error_empty_invalid_skip_stock_continue(self):
        for bad in (TimeoutError('mock'),None,'',0,'NaN','Infinity','bad'):
            worker=self.worker(['123456','654321'])
            self.lookup.current_price.side_effect=[bad,D(100)]
            with self.assertLogs('src.first_rise_breakout.j_protection',level='ERROR'):worker.poll()
            self.assertEqual(self.planner.plan_exits.call_count,1)
            self.assertIn('654321',self.planner.plan_exits.call_args.kwargs['protection'])

    def test_five_second_wait_no_general_cycle(self):
        worker=self.worker([]);stop=Mock();stop.is_set.side_effect=[False,True]
        with patch('src.first_rise_breakout.j_protection.monotonic',side_effect=[0,1]):worker.run(stop)
        stop.wait.assert_called_once_with(4.0)

    def test_existing_rest_adapter_contract(self):
        from src.minute_ma.reference_price import MinuteMaKISReferencePriceLookup
        worker=self.worker(['123456']);client=Mock()
        client.get.return_value={'output':{'stck_prpr':'99900'}}
        worker.price_lookup=MinuteMaKISReferencePriceLookup(client);worker.poll()
        self.assertEqual(client.get.call_args.kwargs['tr_id'],'FHKST01010100')
        self.assertEqual(client.get.call_args.kwargs['params']['FID_INPUT_ISCD'],'123456')
        self.assertEqual(self.planner.plan_exits.call_args.kwargs['protection']['123456'][0],99900)


class ProtectionE2E(WiringE2E):
    def protect(self,price=1030):
        worker=ActualStopProtection(planner=self.live.planner,
            price_lookup=SimpleNamespace(current_price=lambda _:price),submitter=self.live.submitter,
            clock=lambda:self.now,session_open=lambda _:True)
        result=worker.poll();self.c.commit();return result

    def opened(self):
        self.cycle();self.fill('1');self.cycle()

    def test_protection_equal_above_exact_once_and_market_independence(self):
        self.opened()
        self.assertEqual(self.protect(1031),0);self.assertEqual(self.protect(1032),0)
        self.assertEqual(self.protect(1030),1)
        self.assertIsNone(self.query('SELECT exit_reason FROM first_rise_j_market_signal')[0][0])
        self.assertEqual(self.protect(1029),0)
        self.market_stop();self.cycle();self.protect(1028)
        self.assertEqual(len(self.posts),2)
        detail=self.query("SELECT detail FROM live_order_request WHERE side='SELL'")[0][0]
        self.assertEqual(D(detail['actual_average_entry_price']),D(1031))
        self.assertEqual(detail['actual_exit_reason'],'ACTUAL_STOP_ENTRY_BREAK_PROTECTION')

    def test_protection_partial_unknown_restart_no_duplicate(self):
        self.opened();self.timeout=True;self.protect();self.restart();self.protect()
        self.assertEqual(len(self.posts),2)
        self.timeout=False;self.fill('2',qty=1,price=1030);self.recovery.poll(at=self.now);self.c.commit()
        self.protect();self.assertEqual(len(self.posts),2)

    def test_quote_failure_does_not_disable_completed_stop(self):
        self.opened()
        with self.assertLogs('src.first_rise_breakout.j_protection',level='ERROR'):self.protect(None)
        self.assertEqual(len(self.posts),1)
        self.market_stop();self.cycle();self.assertEqual(len(self.posts),2)

    def test_cancelled_protection_recovers_without_market_exit_after_restart(self):
        from dataclasses import replace
        self.opened();self.protect()
        r=self.records['2'];self.records['2']=replace(r,cancelled=True,remaining_quantity=0)
        self.restart();self.cycle()
        self.assertEqual(len(self.posts),3)
        self.assertIsNone(self.query('SELECT exit_reason FROM first_rise_j_market_signal')[0][0])

    def test_partial_buy_sell_does_not_finalize_before_buy_terminal(self):
        self.cycle();self.fill('1',qty=2);self.cycle();self.protect()
        self.assertEqual(self.posts[-1]['payload']['ORD_QTY'],'2')
        self.fill('2',price=1030);self.recovery.poll(at=self.now);self.c.commit()
        self.assertIsNone(self.query('SELECT provisional_applied_at FROM first_rise_j_live_cost')[0][0])
        self.fill('1');self.cycle();self.protect()
        self.assertEqual(len(self.posts),3)

    def test_other_stock_not_blocked(self):
        self.opened();self.protect();self.add_stock('654321');self.cycle()
        self.assertEqual(self.posts[-1]['payload']['PDNO'],'654321')

    def test_ready_and_submitting_pending_guard(self):
        self.opened();planner=self.live.planner
        self.assertEqual(planner.plan_exits(at=self.now,protection={'123456':(D(1030),self.now)}),1)
        self.c.commit();self.market_stop()
        self.assertEqual(planner.plan_exits(at=self.now),0)
        key=planner.protection_request_keys('123456')[0]
        self.assertIsNotNone(self.store.claim(request_key=key));self.c.commit()
        self.assertEqual(planner.plan_exits(at=self.now,protection={'123456':(D(1029),self.now)}),0)
        self.restart();self.protect();self.assertEqual(len(self.posts),2)

    def test_other_strategy_same_stock_owned_quantity_untouched(self):
        from uuid import uuid4
        self.opened()
        other=self.order(uuid4(),'BUY',quantity=999,strategy='MINUTE_MA')
        before=self.query('SELECT * FROM live_broker_order WHERE broker_order_id=%s',(other,))
        self.protect()
        self.assertEqual(int(self.posts[-1]['payload']['ORD_QTY']),self.records['1'].total_filled_quantity)
        self.assertEqual(self.query('SELECT * FROM live_broker_order WHERE broker_order_id=%s',(other,)),before)

    def test_two_submitters_interleaved_claim_only_one_post_attempt(self):
        from src.first_rise_breakout.j_submit import JSubmitStore
        self.opened()
        self.live.planner.plan_exits(at=self.now,protection={'123456':(D(1030),self.now)})
        self.c.commit();key=self.live.planner.protection_request_keys('123456')[0]
        other=JSubmitStore(self.factory,clock=lambda:self.now,session_open=lambda _:True)
        first=self.store.claim(request_key=key);second=other.claim(request_key=key)
        self.assertIsNotNone(first);self.assertIsNotNone(second)
        self.store.mark_post_attempted(order=first)
        with self.assertRaises(TimeoutError):other.mark_post_attempted(order=second)
        self.assertEqual(self.query("SELECT count(*) FROM live_broker_order_audit WHERE broker_order_id=%s AND event_type='FIRST_RISE_POST_ATTEMPT'",(first.broker_order_id,))[0][0],1)


class ProtectionSecondE2E(continuity.ExitContinuityE2E):
    def first_residual(self,remaining=40):
        self.cycle();self.fill('1');self.cycle()
        ProtectionE2E.protect(self)
        self.assertIsNone(self.query('SELECT exit_reason FROM first_rise_j_market_signal')[0][0])
        self.market_stop();self.cycle()
        n=self.records['2'].order_quantity
        self.fill('2',qty=n-remaining);self.cycle()
        return n

    def test_actual_protection_second_transition(self):
        self.test_b_cancel_before_second_and_fill_during_cancel()

    def test_actual_protection_second_aggregate_ownership(self):
        self.test_c_aggregate_fifo_amount_epoch_and_compound()
