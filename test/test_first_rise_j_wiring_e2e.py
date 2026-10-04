"""TEMP PostgreSQL + fake broker only; calls the production wiring modules."""
import os
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime,timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from test import test_first_rise_j_cost as cost_fixture
from test import test_first_rise_j_epoch as epoch_fixture
from src.first_rise_breakout.j_epoch import DailyCapitalContext
from src.first_rise_breakout.j_live_repository import JLiveRepository
from src.first_rise_breakout.j_live_runtime import JLiveRuntime
from src.first_rise_breakout.j_submit import JSubmitStore,JKISOrderTransport
from src.first_rise_breakout.j_recovery import JRecovery
from src.first_rise_breakout.j_repository import JMarketRepository
from src.first_rise_breakout.j_runtime import JMarketRuntime
from src.first_rise_breakout.models import CandidateState,ResearchState,MinuteBar
from src.daily_ma_v03.actual_submit import DailyMaBrokerSubmitRuntime,InMemoryDailyMaSubmitStore
from src.daily_ma_v03.send_orchestration import DailyMaSendOrchestrator
from src.daily_ma_v03.kis_order_history import DailyMaBrokerHistoryOrder


def at(h,m):return datetime(2026,10,2,h,m)
def bar(h,m,high,low=None):return MinuteBar(at(h,m),D(high),D(high),D(low or high),D(high))


def completed_fixture(bars):
    """Dense completed input for V2 liquidity; no future OHLC interpolation."""
    rows=sorted(bars,key=lambda b:b.bar_time);result=[]
    for b in rows:
        if result:
            cursor=result[-1].bar_time+timedelta(minutes=1)
            while cursor<b.bar_time:
                previous=result[-1]
                result.append(replace(previous,bar_time=cursor,open_price=previous.close_price,
                    high_price=previous.close_price,low_price=previous.close_price))
                cursor+=timedelta(minutes=1)
        result.append(b)
    return [replace(b,accumulated_amount=D((b.bar_time.hour*60+b.bar_time.minute-539)*1000000000)) for b in result]


@unittest.skipUnless(os.getenv('FIRST_RISE_TEMP_PG_ENV'),'requires TEMP PostgreSQL')
class WiringE2E(cost_fixture.PostgresCostTests):
    load=epoch_fixture.PostgresEpochTests.load
    bind=epoch_fixture.PostgresEpochTests.bind
    query=epoch_fixture.PostgresEpochTests.query
    tearDown=epoch_fixture.PostgresEpochTests.tearDown
    change=epoch_fixture.PostgresEpochTests.change
    finalized=cost_fixture.PostgresCostTests.finalized
    final=cost_fixture.PostgresCostTests.final

    def setUp(self):
        cost_fixture.PostgresCostTests.setUp(self)
        root=Path(__file__).resolve().parents[1]
        sql=(root/'database/ddl/31_live_broker_contract.sql').read_text(encoding='utf-8')
        audit=next(s.strip() for s in sql.split(';') if s.strip().startswith('CREATE TABLE live_broker_order_audit('))
        self.c.execute(audit.replace('CREATE TABLE ','CREATE TEMP TABLE '))
        self.c.execute('CREATE TEMP TABLE first_rise_breakout_candidate_event(candidate_event_id uuid PRIMARY KEY,discovered_at timestamp)')
        for name in ('20261002_first_rise_j_market.sql','20261003_first_rise_j_execution.sql','20261004_first_rise_v2_capacity.sql'):
            sql=(root/'database/migrations'/name).read_text(encoding='utf-8')
            self.c.execute(sql.replace('BEGIN;','').replace('COMMIT;','').replace('CREATE TABLE ','CREATE TEMP TABLE '))
        self.c.execute("INSERT INTO first_rise_j_activation VALUES('FIRST_RISE_J_V1.3',%s,CURRENT_TIMESTAMP)",(at(9,0),))
        self.c.execute("INSERT INTO common_code(group_cd,code,use_yn,attr1,attr2,attr3,attr4,attr5,attr6,attr7) VALUES('FIRST_RISE_CAPACITY','DEFAULT','Y','10','10000000','30','100','3','5','10')")
        self.c.commit()
        self.now=at(9,10);self.cash=D(25000000);self.posts=[];self.records={};self.timeout=False;self.reject=False
        self.bars=[bar(9,0,1030),bar(9,1,1025,1000),bar(9,9,1031,1025)]
        self.source=SimpleNamespace(completed_bars_from_open=lambda **_:completed_fixture(self.bars),
            previous_close=lambda **_:D(1000),discard=lambda **_:None)
        self.market=JMarketRuntime(repository=JMarketRepository(self.pool),minute_source=self.source)
        self.candidate=CandidateState(uuid4(),at(9,1).date(),'123456',ResearchState.DISCOVERED)
        self.c.execute('INSERT INTO first_rise_breakout_candidate_event VALUES(%s,%s)',(self.candidate.candidate_event_id,at(9,1)))
        self.c.commit()
        self.market.register(self.candidate,discovered_at=at(9,1))
        self.config,_=self.load()
        self.market.refresh(at=self.now,config=self.config)
        self.c.commit()
        c=self.c
        @contextmanager
        def factory():yield c
        self.factory=factory
        self.price=SimpleNamespace(current_price=lambda _:D(1031))
        self.available=SimpleNamespace(orderable_cash=lambda **_:SimpleNamespace(amount=self.cash))
        self.history=SimpleNamespace(orders_for_day=lambda **kw:tuple(r for r in self.records.values()
            if r.order_number==kw['order_number']))
        self.restart()

    def post(self,**kw):
        self.posts.append(kw);number=str(len(self.posts))
        side='BUY' if kw['tr_id']=='TTTC0012U' else 'SELL'
        qty=int(kw['payload']['ORD_QTY'])
        self.records[number]=DailyMaBrokerHistoryOrder('20261002',number,'TEST',kw['payload']['PDNO'],side,qty,
            D(0),0,D(0),qty,0,False,self.now.strftime('%H%M%S'))
        if self.timeout:raise TimeoutError('mock network loss')
        if self.reject:return {'rt_cd':'1','msg_cd':'MOCK_REJECT'}
        if side=='BUY':self.cash-=D(qty)*1031*(1+D('.000146527'))
        return {'rt_cd':'0','output':{'ODNO':number}}

    def restart(self):
        self.context=DailyCapitalContext(self.repo)
        self.store=JSubmitStore(self.factory,config_provider=lambda:self.context.config,clock=lambda:self.now,session_open=lambda _:True)
        transport=JKISOrderTransport(client=SimpleNamespace(post_once=self.post),
            account=SimpleNamespace(cano='TEST',account_product_code='TEST',custtype='P'),attempt_recorder=self.store)
        submitter=DailyMaSendOrchestrator(submit_store=self.store,submit_runtime=DailyMaBrokerSubmitRuntime(
            store=InMemoryDailyMaSubmitStore(),transport=transport,profile=None))
        self.recovery=JRecovery(self.pool,self.history)
        self.live=JLiveRuntime(context=self.context,planner=JLiveRepository(self.pool),submit_store=self.store,
            submitter=submitter,recovery=self.recovery,price_lookup=self.price,cash_lookup=self.available,
            cost_finalizer=SimpleNamespace(finalize_due=lambda **_:{}))

    def cycle(self):
        result=self.live.cycle(at=self.now);self.c.commit();return result

    def fill(self,number,qty=None,price=1031):
        r=self.records[number];qty=r.order_quantity if qty is None else qty
        self.records[number]=replace(r,total_filled_quantity=qty,total_filled_amount=D(qty)*price,
            average_fill_price=D(price),remaining_quantity=r.order_quantity-qty)

    def market_stop(self):
        self.bars.append(bar(9,10,1032,1020));self.now=at(9,11)
        self.market.refresh(at=self.now,config=self.config);self.c.commit()

    def test_complete_partial_restart_close_and_cost_delta(self):
        self.cycle();self.assertEqual(len(self.posts),1)
        self.fill('1',qty=2);self.restart();self.cycle()
        self.assertEqual(self.query('SELECT buy_quantity FROM first_rise_j_live_cost')[0][0],2)
        self.fill('1');self.cycle();self.market_stop();self.cycle()
        self.assertEqual(len(self.posts),2)
        self.fill('2',qty=1,price=1029);self.restart();self.cycle()
        self.assertIsNone(self.query('SELECT provisional_applied_at FROM first_rise_j_live_cost')[0][0])
        self.fill('2',price=1029);self.cycle()
        trade,net=self.query('SELECT trade_id,provisional_net_realized_pnl FROM first_rise_j_live_cost')[0]
        self.assertEqual(self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch')[0][0],net)
        self.restart();self.cycle()
        self.assertEqual(len(self.posts),2)
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_realized_event')[0][0],1)
        cost_id=self.finalized(trade,actual_cost=31420)
        self.final(cost_id);self.final(cost_id)
        final,gross=self.query('SELECT final_net_realized_pnl,gross_realized_pnl FROM first_rise_j_live_cost')[0]
        self.assertEqual(final,gross-D(31420))
        self.assertEqual(self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch')[0][0],final)

    def test_no_capital_independent_stop_second_and_same_stock_open(self):
        self.cash=0;self.cycle();self.assertEqual(len(self.posts),0)
        self.market_stop()
        self.bars.extend([bar(9,50,1040),bar(9,51,1035,1010),bar(10,0,1041,1035)])
        self.now=at(10,1);self.market.refresh(at=self.now,config=self.config);self.c.commit()
        self.cash=25000000;self.cycle()
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.query('SELECT signal_sequence FROM first_rise_j_market_signal ORDER BY 1'),[(1,),(2,)])

    def test_unknown_and_lost_ack_recovery_never_reposts(self):
        self.timeout=True;self.cycle();self.restart();self.cycle()
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.query('SELECT status FROM live_broker_order')[0][0],'UNKNOWN_BROKER_STATE')
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_live_cost')[0][0],0)

    def test_ack_response_durable_even_if_ack_transaction_fails(self):
        def broken(**kw):raise ConnectionError('mock after POST response persisted')
        self.store.acknowledge=broken
        self.cycle();self.fill('1');self.restart();self.cycle()
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.query('SELECT status FROM live_broker_order')[0][0],'FILLED')

    def test_activation_no_replay_and_reject_keeps_market_open(self):
        self.c.execute('UPDATE first_rise_j_activation SET effective_from=%s',(at(9,10),));self.c.commit()
        self.cycle();self.assertEqual(len(self.posts),0)
        self.c.execute('UPDATE first_rise_j_activation SET effective_from=%s',(at(9,0),));self.c.commit()
        self.reject=True;self.cycle()
        self.assertEqual(self.query('SELECT status FROM live_broker_order')[0][0],'REJECTED')
        self.assertIsNone(self.query('SELECT exit_reason FROM first_rise_j_market_signal')[0][0])
        self.market_stop()
        self.assertEqual(self.query('SELECT exit_reason FROM first_rise_j_market_signal')[0][0],'STOP_ENTRY_BREAK')

    def add_stock(self,stock,discovered=None):
        discovered=discovered or at(9,1)
        candidate=replace(self.candidate,candidate_event_id=uuid4(),stock_code=stock)
        self.c.execute('INSERT INTO first_rise_breakout_candidate_event VALUES(%s,%s)',(candidate.candidate_event_id,discovered))
        self.c.commit();self.market.register(candidate,discovered_at=discovered)
        self.market.refresh(at=self.now,config=self.config);self.c.commit()

    def test_pending_reservation_small_last_slot_and_serialized_cash_lookup(self):
        import psycopg
        from src.repository.database import DatabaseSettings
        self.add_stock('234567');self.add_stock('345678')
        self.context.load(at=self.now)
        # Independent connection checks the planner owns the global capital lock
        # throughout the injected broker-cash lookup. No tables on this connection.
        with psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs()) as other:
            def cash(**kwargs):
                locked=other.execute("SELECT pg_try_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))").fetchone()[0]
                other.rollback()
                self.assertFalse(locked)
                return SimpleNamespace(amount=D(25000000))
            for signal in self.live.planner.entry_signals(at=self.now):
                self.live.planner.plan_buy(signal,context=self.context,at=self.now,
                    price_lookup=self.price,cash_lookup=SimpleNamespace(orderable_cash=cash))
            self.c.commit()
        quantities=[r[0] for r in self.query('SELECT requested_quantity FROM live_order_request ORDER BY created_at,execution_stock_code')]
        self.assertEqual(quantities[0],quantities[1])
        self.assertTrue(0<quantities[2]<quantities[1])
        self.assertLessEqual(self.query('SELECT sum(reserved_capital) FROM live_order_request')[0][0],25000000)
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_capital_epoch')[0][0],1)

    def test_second_market_preserved_while_first_actual_position_remains_open(self):
        self.cycle();self.fill('1',qty=1);self.cycle();self.market_stop()
        self.bars.extend([bar(9,50,1040),bar(9,51,1035,1010),bar(10,0,1041,1035)])
        self.now=at(10,1);self.market.refresh(at=self.now,config=self.config);self.c.commit()
        self.cycle()
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.query("SELECT count(*) FROM first_rise_j_live_intent WHERE side='BUY'")[0][0],1)
        self.fill('1');self.cycle()
        self.assertEqual(len(self.posts),2) # valid SECOND, FIRST actual residual remains
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_market_signal')[0][0],2)

    def test_old_epoch_close_and_final_delta_remain_in_old_epoch(self):
        self.cycle();self.fill('1');self.cycle();self.market_stop();self.cycle()
        old_epoch=self.query('SELECT epoch_id FROM first_rise_j_capital_binding')[0][0]
        self.change(attr5='20000000');_,new_epoch=self.load(day=1)
        self.fill('2',price=1100);self.restart();self.cycle()
        trade=self.query('SELECT trade_id FROM first_rise_j_live_cost')[0][0]
        cost_id=self.finalized(trade);self.final(cost_id)
        self.assertNotEqual(old_epoch,new_epoch.epoch_id)
        self.assertEqual(self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch WHERE epoch_id=%s',(new_epoch.epoch_id,))[0][0],0)

    def test_other_strategy_order_identity_cannot_be_consumed(self):
        self.cycle()
        self.c.execute('''INSERT INTO live_broker_order(broker_order_id,order_request_id,strategy_instance_id,
            execution_stock_code,side,quantity,client_order_key,status,payload,broker_order_number)
            VALUES(%s,%s,'MINUTE_MA','123456','BUY',100,%s,'FILLED','{}','OTHER')''',(uuid4(),uuid4(),str(uuid4())))
        self.c.commit()
        self.fill('1');self.cycle();self.market_stop();self.cycle()
        self.assertEqual(int(self.posts[-1]['payload']['ORD_QTY']),self.records['1'].order_quantity)
        self.assertEqual(self.query("SELECT quantity FROM live_broker_order WHERE strategy_instance_id='MINUTE_MA'")[0][0],100)

    def test_session_close_no_second(self):
        self.bars.append(bar(15,30,1031))
        self.now=at(15,31);self.market.refresh(at=self.now,config=self.config);self.c.commit()
        self.assertEqual(self.query('SELECT exit_reason FROM first_rise_j_market_signal')[0][0],'SESSION_CLOSE')
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_market_signal')[0][0],1)

    def test_book_profit_no_second(self):
        def scaled(minute,o,h,l,c):
            return MinuteBar(at(9,minute),*(D(str(v))*D('10.31') for v in (o,h,l,c)))
        self.bars.extend(scaled(m,101,102,100,101.5) for m in range(12,39))
        self.bars.extend([scaled(39,103,104,101,102.5),scaled(40,102.5,103,100.5,101.5),
                          scaled(41,101.5,102,100.5,101),scaled(42,100.8,101,100,100.5)])
        self.now=at(9,43);self.market.refresh(at=self.now,config=self.config);self.c.commit()
        self.assertEqual(self.query('SELECT exit_reason FROM first_rise_j_market_signal')[0][0],'BOOK_TENKAN_PROFIT')
        self.bars.extend([bar(9,50,1080),bar(9,51,1070,1060),bar(10,0,1081,1070)])
        self.now=at(10,1);self.market.refresh(at=self.now,config=self.config);self.c.commit()
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_market_signal')[0][0],1)

    def test_close_tier_used_by_next_new_signal(self):
        self.cycle();self.fill('1');self.cycle();self.market_stop();self.cycle()
        self.fill('2',price=2200);self.cycle()
        self.assertEqual(self.query('SELECT common_slot_amount FROM first_rise_j_capital_epoch')[0][0],20000000)
        old_binding=self.query('SELECT entry_sizing_evidence FROM first_rise_j_capital_binding')[0][0]
        self.bars.extend([bar(9,15,1050),bar(9,16,1040,1025),bar(9,25,1051,1040)])
        self.now=at(9,26);self.add_stock('234567',discovered=at(9,12));self.cash=30000000;self.cycle()
        self.assertEqual(self.query("SELECT sizing_evidence->>'common_slot_amount' FROM first_rise_j_live_intent WHERE side='BUY' ORDER BY created_at")[-1][0],'20000000')
        self.assertEqual(old_binding['common_slot_amount'],'10000000')

    def test_restart_after_claim_before_post_can_finish_once(self):
        self.context.load(at=self.now)
        signal=self.live.planner.entry_signals(at=self.now)[0]
        self.live.planner.plan_buy(signal,context=self.context,at=self.now,price_lookup=self.price,cash_lookup=self.available)
        key=self.store.discover_ready_request_keys()[0]
        self.assertIsNotNone(self.store.claim(request_key=key));self.c.commit()
        self.restart();self.cycle();self.restart();self.cycle()
        self.assertEqual(len(self.posts),1)


if __name__=='__main__':unittest.main()
