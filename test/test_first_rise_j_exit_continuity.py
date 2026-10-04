"""Final same-stock continuity contract: TEMP PostgreSQL and fake KIS only."""
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4
from test.test_first_rise_j_wiring_e2e import WiringE2E,at,bar
from src.first_rise_breakout.j_cancel import JCancelRuntime
from src.first_rise_breakout.j_recovery import JRecovery


class ExitContinuityE2E(WiringE2E):
    def setUp(self):
        super().setUp()
        self.cancel_posts=[];self.cancel_unknown=False;self.cancel_extra=0
        self.open_session=True
        self.install_cancel()

    def install_cancel(self):
        def get(**kw):
            self.assertEqual(kw['tr_id'],'TTTC0084R')
            self.assertEqual(kw['params']['INQR_DVSN_2'],'1')
            return {'output':[{'odno':n,'pdno':r.stock_code,'psbl_qty':str(r.remaining_quantity),'ord_gno_brno':'BRANCH'}
                for n,r in self.records.items() if r.side=='SELL' and not r.cancelled and r.remaining_quantity>0]}
        def post(**kw):
            self.cancel_posts.append(kw)
            if self.cancel_unknown:raise TimeoutError('cancel unknown')
            number=kw['payload']['ORGN_ODNO'];r=self.records[number]
            if self.cancel_extra:
                self.fill(number,qty=r.total_filled_quantity+self.cancel_extra)
                r=self.records[number]
            self.records[number]=replace(r,cancelled=True,remaining_quantity=0)
            return {'rt_cd':'0','output':{'ODNO':'CANCEL-1'}}
        client=SimpleNamespace(get=get,post_once=post,last_response_headers={})
        self.live.cancellations=JCancelRuntime(self.pool,client,
            SimpleNamespace(cano='TEST',account_product_code='TEST',custtype='P'),session_open=lambda _:self.open_session)
        self.store.session_open=lambda _:self.open_session

    def first_residual(self,remaining=40):
        self.cycle();self.fill('1');self.cycle();self.market_stop();self.cycle()
        n=self.records['2'].order_quantity
        self.fill('2',qty=n-remaining);self.cycle()
        return n

    def terminal(self,number):
        self.records[number]=replace(self.records[number],cancelled=True,remaining_quantity=0)

    def second(self):
        self.bars.extend([bar(9,50,1040),bar(9,51,1035,1010),bar(10,0,1041,1035)])
        self.now=at(10,1);self.market.refresh(at=self.now,config=self.config);self.c.commit()
        self.cash=D(103100)*(1+D('.000146527'))

    def second_stop(self):
        self.bars.append(bar(10,1,1042,1030));self.now=at(10,2)
        self.market.refresh(at=self.now,config=self.config);self.c.commit()

    def test_a_first_terminal_residual_recovery_once(self):
        self.first_residual();self.terminal('2');self.cycle()
        self.assertEqual(self.records['3'].order_quantity,40)
        self.restart();self.install_cancel();self.cycle();self.assertEqual(len(self.posts),3)
        self.fill('3');self.cycle()
        self.assertEqual(self.query('SELECT sum(buy_quantity-sell_quantity) FROM first_rise_j_live_cost')[0][0],0)

    def test_b_cancel_before_second_and_fill_during_cancel(self):
        self.first_residual();self.second();self.cancel_extra=10;self.cycle()
        self.assertEqual(len(self.cancel_posts),1)
        self.assertEqual(self.query("SELECT status FROM first_rise_j_cancel_request")[0][0],'CONFIRMED')
        self.assertEqual(self.records['3'].side,'BUY')
        self.assertEqual(self.query('SELECT buy_quantity-sell_quantity FROM first_rise_j_live_cost')[0][0],30)
        self.fill('3');self.cycle()
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_live_cost')[0][0],2)

    def test_c_aggregate_fifo_amount_epoch_and_compound(self):
        self.first_residual();self.second();self.cycle();self.fill('3');self.cycle();self.second_stop();self.cycle()
        self.assertEqual(self.records['4'].order_quantity,140)
        self.fill('4',qty=60,price=1041);self.cycle()
        self.assertEqual([r[0] for r in self.query('''SELECT x.delta_quantity FROM first_rise_j_live_checkpoint_allocation x
            JOIN live_broker_order o ON o.broker_order_id=x.broker_order_id
            WHERE o.broker_order_number='4' ORDER BY x.cost_trade_id''')],[40,20])
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_live_cost WHERE provisional_applied_at IS NOT NULL')[0][0],1)
        self.fill('4',price=1041);self.cycle();self.restart();self.install_cancel();self.cycle()
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_live_cost WHERE provisional_applied_at IS NOT NULL')[0][0],2)
        self.assertEqual(self.query('SELECT sum(provisional_net_realized_pnl) FROM first_rise_j_live_cost')[0][0],
            self.query('SELECT sum(realized_net_pnl) FROM first_rise_j_capital_epoch')[0][0])

    def test_d_second_exit_recovery_restart_to_zero(self):
        self.first_residual();self.second();self.cycle();self.fill('3');self.cycle();self.second_stop();self.cycle()
        self.fill('4',qty=50);self.terminal('4');self.cycle()
        self.assertEqual(self.records['5'].order_quantity,90)
        self.fill('5',qty=20);self.restart();self.install_cancel();self.cycle();self.terminal('5');self.cycle()
        self.assertEqual(self.records['6'].order_quantity,70)
        self.fill('6');self.cycle();self.cycle()
        self.assertEqual(len(self.posts),6)
        self.assertEqual(self.query('SELECT sum(buy_quantity-sell_quantity) FROM first_rise_j_live_cost')[0][0],0)

    def test_e_unknown_cancel_never_reposts_and_second_waits(self):
        self.first_residual();self.second();self.cancel_unknown=True;self.cycle()
        self.assertEqual(len(self.posts),2)
        self.restart();self.install_cancel();self.cycle()
        self.assertEqual(len(self.cancel_posts),1)
        self.terminal('2');self.cycle();self.assertEqual(len(self.posts),3)

    def test_f_other_stock_unaffected_by_exit_recovery(self):
        self.first_residual()
        from src.first_rise_breakout.models import CandidateState,ResearchState
        candidate=CandidateState(uuid4(),at(9,1).date(),'234567',ResearchState.DISCOVERED)
        self.c.execute('INSERT INTO first_rise_breakout_candidate_event VALUES(%s,%s)',(candidate.candidate_event_id,at(9,1)))
        self.c.commit();self.market.register(candidate,discovered_at=at(9,1))
        from test.test_first_rise_j_wiring_e2e import completed_fixture
        self.source.completed_bars_from_open=lambda **_:completed_fixture(self.bars[:3])
        self.market.refresh(at=self.now,config=self.config);self.c.commit();self.cycle()
        self.assertEqual(self.posts[-1]['payload']['PDNO'],'234567')

    def test_g_unknown_buy_reservation_not_global_block(self):
        self.timeout=True;self.cycle();self.timeout=False
        self._new_stock()
        self.assertEqual(len(self.posts),2)
        self.assertEqual(self.posts[-1]['payload']['PDNO'],'234567')
        self.assertGreater(int(self.posts[-1]['payload']['ORD_QTY']),0)

    def _new_stock(self):
        from src.first_rise_breakout.models import CandidateState,ResearchState
        candidate=CandidateState(uuid4(),at(9,1).date(),'234567',ResearchState.DISCOVERED)
        self.c.execute('INSERT INTO first_rise_breakout_candidate_event VALUES(%s,%s)',(candidate.candidate_event_id,at(9,1)))
        self.c.commit();self.market.register(candidate,discovered_at=at(9,1))
        self.market.refresh(at=self.now,config=self.config);self.c.commit();self.cycle()

    def test_session_closed_preserves_recovery_then_resumes(self):
        self.first_residual();self.terminal('2');self.open_session=False;self.cycle()
        self.assertEqual(len(self.posts),2)
        self.restart();self.install_cancel();self.open_session=True;self.cycle()
        self.assertEqual(self.records['3'].order_quantity,40)

    def test_unsent_recovery_is_superseded_by_second(self):
        self.first_residual();self.terminal('2');self.open_session=False;self.cycle()
        self.assertEqual(len(self.posts),2)
        self.second();self.open_session=True;self.cycle()
        self.assertEqual(self.records['3'].side,'BUY')
        self.assertEqual(self.query("SELECT count(*) FROM live_order_request WHERE reason='SECOND_SUPERSEDES_UNSENT_SELL'")[0][0],1)

    def test_second_arrives_between_claim_and_post(self):
        self.first_residual();self.terminal('2');self.open_session=False;self.cycle()
        self.open_session=True
        key=self.store.discover_ready_request_keys()[0]
        order=self.store.claim(request_key=key)
        self.second()
        with self.assertRaisesRegex(TimeoutError,'SECOND_SUPERSEDES'):
            self.store.mark_post_attempted(order=order)
        self.cycle();self.assertEqual(self.records['3'].side,'BUY')

    def test_unknown_sell_never_reposts_or_allows_second(self):
        self.cycle();self.fill('1');self.cycle();self.market_stop();self.timeout=True;self.cycle();self.timeout=False
        self.second();self.restart();self.install_cancel();self.cycle();self.cycle()
        self.assertEqual(len(self.posts),2)
        self.assertEqual(len(self.cancel_posts),0)
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_market_signal')[0][0],2)

    def test_fifo_fractional_amount_conservation(self):
        self.first_residual();self.second();self.cycle();self.fill('3');self.cycle();self.second_stop();self.cycle()
        r=self.records['4']
        self.records['4']=replace(r,total_filled_quantity=61,total_filled_amount=D('63111.01'),
            average_fill_price=D('63111.01')/61,remaining_quantity=79)
        self.cycle()
        self.assertEqual(self.query('''SELECT sum(x.delta_quantity),sum(x.delta_amount) FROM first_rise_j_live_checkpoint_allocation x
            JOIN live_broker_order o ON o.broker_order_id=x.broker_order_id WHERE o.broker_order_number='4' ''')[0],(61,D('63111.01')))

    def test_unclassified_rejection_persists_and_never_reposts(self):
        self.first_residual()
        r=self.records['2']
        self.records['2']=replace(r,rejected_quantity=40,remaining_quantity=0)
        self.cycle();self.assertEqual(len(self.posts),2)
        # Old/ad-hoc flags cannot create a retry allowlist.
        self.c.execute("UPDATE live_order_request SET detail=detail || '{\"retryable_rejection\":true}'::jsonb WHERE side='SELL'")
        self.c.commit();self.cycle();self.cycle()
        self.assertEqual(len(self.posts),2)
        evidence=self.query("SELECT detail FROM live_order_request WHERE side='SELL'")[0][0]
        self.assertEqual(evidence['exit_state'],'EXIT_RECOVERY')
        self.assertEqual(evidence['remaining_owned_quantity'],40)

    def test_cancelled_terminal_to_second_without_recovery_sell(self):
        self.first_residual();self.terminal('2');self.second();self.cycle()
        self.assertEqual(len(self.cancel_posts),0)
        self.assertEqual(self.records['3'].side,'BUY')
        self.assertEqual(len(self.posts),3)

    def test_rejected_terminal_to_second_without_retry_classification(self):
        self.first_residual()
        self.records['2']=replace(self.records['2'],rejected_quantity=40,remaining_quantity=0)
        self.second();self.cycle()
        self.assertEqual(len(self.cancel_posts),0)
        self.assertEqual(self.records['3'].side,'BUY')
        self.assertEqual(len(self.posts),3)

    def test_second_no_capital_holds_first_until_market_exit(self):
        self.first_residual();self.terminal('2');self.second();self.cash=D(0);self.cycle();self.cycle()
        self.assertEqual(len(self.posts),2)
        self.assertEqual(self.query("SELECT planning_reason FROM first_rise_j_live_intent WHERE side='BUY' ORDER BY signal_time")[-1][0],'NO_CAPITAL')
        self.restart();self.install_cancel();self.cycle();self.assertEqual(len(self.posts),2)
        self.second_stop();self.cycle()
        self.assertEqual(self.records['3'].side,'SELL')
        self.assertEqual(self.records['3'].order_quantity,40)

    def test_unknown_known_identity_reconcile_cancel_then_second(self):
        self.first_residual();self.second()
        self.c.execute("UPDATE live_broker_order SET status='UNKNOWN_BROKER_STATE' WHERE side='SELL'")
        self.c.execute("UPDATE live_order_request SET status='UNKNOWN_BROKER_STATE' WHERE side='SELL'")
        self.c.commit();self.restart();self.install_cancel();self.cycle()
        self.assertEqual(len(self.cancel_posts),1)
        self.assertEqual(self.records['3'].side,'BUY')

    def test_extra_fill_30_plus_second_100_aggregate_130(self):
        self.first_residual();self.second();self.cancel_extra=10;self.cycle();self.fill('3');self.cycle()
        self.second_stop();self.cycle()
        self.assertEqual(self.records['4'].order_quantity,130)
        self.fill('4',qty=50);self.cycle()
        self.assertEqual([r[0] for r in self.query('''SELECT x.delta_quantity FROM first_rise_j_live_checkpoint_allocation x
            JOIN live_broker_order o ON o.broker_order_id=x.broker_order_id WHERE o.broker_order_number='4' ORDER BY x.cost_trade_id''')],[30,20])
        self.fill('4');self.cycle()
        self.assertEqual(self.query('SELECT sum(buy_quantity-sell_quantity) FROM first_rise_j_live_cost')[0][0],0)

    def test_sell_post_rejection_raw_codes_and_remaining_owned_durable(self):
        self.cycle();self.fill('1');self.cycle();self.market_stop()
        # The fake broker POST rejects; no real HTTP request is possible.
        self.reject=True;self.cycle();self.cycle()
        evidence=self.query("SELECT detail FROM live_order_request WHERE side='SELL'")[0][0]
        self.assertEqual(evidence['rejection_evidence']['rt_cd'],'1')
        self.assertEqual(evidence['rejection_evidence']['msg_cd'],'MOCK_REJECT')
        self.assertIn('msg1',evidence['rejection_evidence'])
        self.assertEqual(evidence['remaining_owned_quantity'],self.records['1'].order_quantity)
        self.assertEqual(len(self.posts),2)
