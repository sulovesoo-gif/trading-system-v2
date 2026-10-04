import os
import unittest
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from psycopg.types.json import Jsonb
from src.first_rise_breakout.j_submit import JSubmitStore, JKISOrderTransport
from src.daily_ma_v03.actual_submit import DailyMaBrokerSubmitRuntime, InMemoryDailyMaSubmitStore
from src.daily_ma_v03.send_orchestration import DailyMaSendOrchestrator
from test import test_first_rise_j_epoch as epoch_fixture

DAY=epoch_fixture.DAY.replace(hour=9,minute=30)


@unittest.skipUnless(os.getenv('FIRST_RISE_TEMP_PG_ENV'),'requires TEMP PostgreSQL')
class PostgresSubmitTests(unittest.TestCase):
    load=epoch_fixture.PostgresEpochTests.load
    bind=epoch_fixture.PostgresEpochTests.bind
    query=epoch_fixture.PostgresEpochTests.query
    tearDown=epoch_fixture.PostgresEpochTests.tearDown

    def setUp(self):
        epoch_fixture.PostgresEpochTests.setUp(self)
        root=Path(__file__).resolve().parents[1]
        for filename,tables in [('30_live_order_planning.sql',('live_order_request',)),
                                ('31_live_broker_contract.sql',('live_broker_order','live_broker_order_audit'))]:
            text=(root/'database/ddl'/filename).read_text(encoding='utf-8')
            for table in tables:
                statement=next(s.strip() for s in text.split(';') if s.strip().startswith('CREATE TABLE '+table+'('))
                self.c.execute(statement.replace('CREATE TABLE ','CREATE TEMP TABLE ',1))
        self.c.execute('CREATE TEMP TABLE first_rise_j_market_signal(market_signal_id uuid PRIMARY KEY,exit_reason text)')
        self.c.execute('CREATE TEMP TABLE first_rise_j_live_cost(trade_id uuid PRIMARY KEY,buy_quantity integer,sell_quantity integer)')
        self.c.execute('CREATE TEMP TABLE first_rise_j_sell_allocation(order_request_id uuid,trade_id uuid,planned_quantity integer)')
        sql=(root/'database/migrations/20261003_first_rise_j_execution.sql').read_text(encoding='utf-8')
        self.c.execute(sql.replace('BEGIN;','').replace('COMMIT;','').replace('CREATE TABLE ','CREATE TEMP TABLE '))
        self.c.commit()
        self.config,self.epoch=self.load()
        self.now=DAY
        c=self.c
        @contextmanager
        def factory():
            yield c
        self.factory=factory
        self.store=JSubmitStore(factory,config_provider=lambda:self.config,clock=lambda:self.now,session_open=lambda _:True)
        self.posts=[]

    def request(self, *, side='BUY', signal_time=DAY, activate=True):
        trade=self.bind(self.epoch)
        signal,intent,request=uuid4(),uuid4(),uuid4()
        key=str(request)
        with self.c.transaction():
            if activate:
                self.c.execute('INSERT INTO first_rise_j_activation(strategy_id,effective_from) VALUES(%s,%s) ON CONFLICT DO NOTHING',
                    ('FIRST_RISE_J_V1.3',DAY-timedelta(seconds=1)))
            self.c.execute('INSERT INTO first_rise_j_market_signal(market_signal_id) VALUES(%s)',(signal,))
            if side=='SELL':self.c.execute('INSERT INTO first_rise_j_live_cost VALUES(%s,1,0)',(trade,))
            self.c.execute('''INSERT INTO live_order_request(order_request_id,idempotency_key,strategy_instance_id,
                source_intent_id,source_decision_id,execution_stock_code,side,requested_notional,requested_quantity,
                reference_price,order_type,execution_target_time,strategy_capital_before,reserved_capital,
                safety_status,status,reason,detail)
                VALUES(%s,%s,'FIRST_RISE_J_V1.3',%s,%s,'123456',%s,10000,1,10000,'MARKET',%s,10000000,10000,
                'VALIDATED','READY_FOR_BROKER','TEST',%s)''',
                (request,key,intent,signal,side,signal_time,Jsonb({'first_rise_trade_id':str(trade)})))
            self.c.execute('INSERT INTO first_rise_j_live_intent(intent_id,market_signal_id,trade_id,side,signal_time,order_request_id) VALUES(%s,%s,%s,%s,%s,%s)',
                (intent,signal,trade,side,signal_time,request))
            if side=='SELL':self.c.execute('INSERT INTO first_rise_j_sell_allocation VALUES(%s,%s,1)',(request,trade))
        return key

    def submitter(self, response=None, timeout=False):
        def post(**kwargs):
            self.posts.append(kwargs)
            if timeout:raise TimeoutError('fixture unknown')
            return response or {'rt_cd':'0','output':{'ODNO':'fixture-order'}}
        transport=JKISOrderTransport(client=SimpleNamespace(post_once=post),
            account=SimpleNamespace(cano='TEST',account_product_code='TEST',custtype='P'),attempt_recorder=self.store)
        runtime=DailyMaBrokerSubmitRuntime(store=InMemoryDailyMaSubmitStore(),transport=transport,profile=None)
        return DailyMaSendOrchestrator(submit_store=self.store,submit_runtime=runtime)

    def test_ack_restart_duplicate_zero(self):
        key=self.request()
        self.assertEqual(self.submitter().process_request(key)[1],'ACK')
        self.assertEqual(self.submitter().process_request(key)[1],'RESEND_FORBIDDEN')
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.query('SELECT status FROM live_broker_order')[0][0],'ACCEPTED')

    def test_unknown_restart_never_reposts(self):
        key=self.request()
        self.assertEqual(self.submitter(timeout=True).process_request(key)[1],'UNKNOWN_BROKER_STATE')
        self.assertEqual(self.submitter().process_request(key)[1],'RESEND_FORBIDDEN')
        self.assertEqual(len(self.posts),1)

    def test_reject_and_missing_ack_number(self):
        key=self.request()
        self.assertEqual(self.submitter(response={'rt_cd':'1'}).process_request(key)[1],'REJECTED')
        self.assertEqual(self.query('SELECT status FROM live_order_request')[0][0],'REJECTED')
        key=self.request()
        self.submitter(response={'rt_cd':'0','output':{}}).process_request(key)
        self.assertIn(('UNKNOWN_BROKER_STATE',),self.query('SELECT status FROM live_broker_order'))

    def test_activation_and_expired_buy_blocked_sell_restores(self):
        key=self.request(activate=False)
        self.assertIsNone(self.store.claim(request_key=key))
        key=self.request(signal_time=DAY-timedelta(days=1))
        self.assertIsNone(self.store.claim(request_key=key))
        self.now=DAY.replace(hour=15,minute=0)
        key=self.request()
        self.assertIsNone(self.store.claim(request_key=key))
        self.config=None
        key=self.request(side='SELL',signal_time=DAY-timedelta(days=1))
        self.assertIsNotNone(self.store.claim(request_key=key))

    def test_attempt_record_is_once_even_direct_transport_retry(self):
        order=self.store.claim(request_key=self.request())
        self.store.mark_post_attempted(order=order)
        with self.assertRaises(TimeoutError):self.store.mark_post_attempted(order=order)
        self.assertEqual(self.query("SELECT count(*) FROM live_broker_order_audit WHERE event_type='FIRST_RISE_POST_ATTEMPT'")[0][0],1)


if __name__=='__main__':unittest.main()
