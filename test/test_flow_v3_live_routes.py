"""Pure tests + opt-in DISPOSABLE PostgreSQL tests. Never load production .env/KIS clients."""
import os
import json
import unittest
from contextlib import contextmanager
from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from src.flow_v3.live_contract import ROUTES,execution_product,validate_mapping,order_quantity,request_payload
from src.flow_v3.preorder import check_buy,FlowCashCheck
from src.flow_v3.live_transport import FlowTransport
from test.flow_v3_legacy_fixture import WHITELIST

ROOT=Path(__file__).resolve().parents[1]


class RouteUnitTests(unittest.TestCase):
    def test_six_mappings_and_short_underlying_denied(self):
        for (stock,direction,route),code in ROUTES.items():
            self.assertEqual(execution_product(stock,direction,route),code)
            validate_mapping('DB_APPROVED_ID',stock,direction,code,route)
            self.assertEqual(request_payload(code,'BUY',2)['body']['EXCG_ID_DVSN_CD'],'KRX')
        for stock in ('000660','005930'):
            self.assertRaises(ValueError,execution_product,stock,'SHORT','UNDERLYING')
        self.assertRaises(ValueError,execution_product,'UNKNOWN','LONG','UNDERLYING')

    def test_variable_capital_no_cap(self):
        self.assertEqual([order_quantity(c,250000) for c in (6000000,6500000,5500000,10000000)],[24,26,22,40])

    def test_cash_not_virtual_sum_all_routes(self):
        self.assertIsNone(check_buy('UNDERLYING',100,100))
        self.assertEqual(check_buy('UNDERLYING',100,99),'KIS_ORDERABLE_CASH_INSUFFICIENT')
        for route in ('LEVERAGE','INVERSE'):
            self.assertIsNone(check_buy(route,100,100))
            self.assertEqual(check_buy(route,100,99),'KIS_ORDERABLE_CASH_INSUFFICIENT')
        for cash in (None,Decimal('NaN'),Decimal('Infinity'),-1):
            self.assertIsNotNone(check_buy('UNDERLYING',100,cash))

    def test_same_account_cash_lookup_all_routes(self):
        calls=[]
        client=SimpleNamespace(get=lambda **kw:(calls.append(kw) or {'output':{'nrcvb_buy_amt':'500000'}}))
        account=SimpleNamespace(cano='TEST_ACCOUNT',account_product_code='01')
        check=FlowCashCheck(client,account)
        self.assertIsNone(check('000660','UNDERLYING',250000,2))
        self.assertIsNotNone(check('005930','UNDERLYING',250000,3))
        self.assertIsNone(check('0193T0','LEVERAGE',1000,1))
        self.assertIsNone(check('0197X0','INVERSE',1000,1))
        self.assertEqual(len(calls),4)
        self.assertEqual({c['params']['CANO'] for c in calls},{'TEST_ACCOUNT'})


@unittest.skipUnless(os.getenv('FLOW_ROUTE_TEST_DSN'),'isolated DB opt-in')
class RouteDatabaseTests(unittest.TestCase):
    def test_migration_and_full_lifecycle(self):
        import psycopg
        from src.flow_v3.live_operations import LiveOperations
        from src.flow_v3.live_repository import LiveRepository
        from psycopg.conninfo import conninfo_to_dict
        dsn=os.environ['FLOW_ROUTE_TEST_DSN'];config=conninfo_to_dict(dsn)
        self.assertEqual(config.get('dbname'),'flow_route_test')
        self.assertEqual(config.get('host'),'127.0.0.1')
        self.assertEqual(config.get('port'),'55491')
        c=psycopg.connect(dsn,autocommit=True)
        self.addCleanup(c.close)
        self.assertIsNone(c.execute("SELECT to_regclass('flow_v3_strategy_master')").fetchone()[0],
                          'Requires a fresh disposable database; never cleans an existing DB')
        c.execute((ROOT/'test/fixtures/flow_v3_routes_legacy.sql').read_text())
        for name in ('20260909_flow_v3_live_preparation.sql','20260909_flow_v3_live_pipeline.sql','20260910_flow_v3_send_authorization.sql'):
            c.execute((ROOT/'database/migrations'/name).read_text())
        now=datetime(2026,9,13,9)
        for sid,(direction,code) in WHITELIST.items():
            c.execute('INSERT INTO flow_v3_strategy_master VALUES(%s,%s,%s,%s,%s)',(sid,'000660',direction,code,'Y'))
            op=c.execute("""INSERT INTO flow_v3_strategy_operation(strategy_id,operation_status,allocated_amount,
                capital_epoch_no,effective_from,change_reason,changed_by) VALUES(%s,'LIVE',16732.5,1,%s,'LEGACY','TEST') RETURNING operation_id""",(sid,now)).fetchone()[0]
            c.execute("""INSERT INTO flow_v3_live_preparation(strategy_id,direction,stock_code,execution_code,
                approval_reference,initial_price_date,initial_close,initial_capital,current_capital,reference_price,next_quantity)
                VALUES(%s,%s,'000660',%s,'LEGACY','2026-09-12',11155,16732.5,16732.5,11155,1)""",(sid,direction,code))
            c.execute("""INSERT INTO flow_v3_live_capital(strategy_id,operation_id,initial_price_date,initial_close,
                initial_capital,current_capital,activated_at) VALUES(%s,%s,'2026-09-12',11155,16732.5,16732.5,%s)""",(sid,op,now))
        for sid,direction,code in [('SAMSUNG_LONG','LONG','0193W0'),('SAMSUNG_SHORT','SHORT','0193L0')]:
            c.execute("INSERT INTO flow_v3_strategy_master VALUES(%s,'005930',%s,%s,'Y')",(sid,direction,code))
        eid=0
        def event(sid,t):
            nonlocal eid
            eid+=1
            stock,direction,code=c.execute('SELECT stock_code,direction,execution_code FROM flow_v3_strategy_master WHERE strategy_id=%s',(sid,)).fetchone()
            c.execute('INSERT INTO flow_v3_paper_trade VALUES(%s,%s)',(eid,'UNMODIFIED_PAPER'))
            c.execute("INSERT INTO flow_v3_runtime_entry_event VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'SIGNAL_HOLD','F1',1,3)",
                      (eid,sid,'E'+str(eid),eid,t,t,stock,direction,code))
            return eid
        from src.flow_v3.live_repository import identity
        for sid,count in [('FV3008084',25),('FV3008209',32)]:
            for n in range(count):
                e=event(sid,now-timedelta(days=1)+timedelta(minutes=n));iid=identity('legacy'+str(e))
                c.execute("""INSERT INTO flow_v3_live_intent(intent_id,strategy_id,event_id,entry_event_key,paper_trade_id,
                    side,lifecycle_key,signal_time,execution_not_before,execution_code,quantity,status)
                    VALUES(%s,%s,%s,%s,%s,'BUY',%s,%s,%s,'0193T0',1,'READY_NO_SEND')""",
                    (iid,sid,e,'E'+str(e),e,'LEGACY_KEY'+str(e),now-timedelta(days=1),now-timedelta(days=1)))
                c.execute("""INSERT INTO flow_v3_live_order(broker_order_id,intent_id,request_payload,status)
                    VALUES(%s,%s,'{}','READY_NO_SEND')""",(identity('order'+str(e)),iid))
        tables=['flow_v3_strategy_master','flow_v3_paper_trade','flow_v3_strategy_operation','flow_v3_live_capital',
                'flow_v3_live_preparation','flow_v3_live_intent','flow_v3_live_order','flow_v3_live_trade',
                'flow_v3_live_lot','flow_v3_live_settlement','flow_v3_live_checkpoint_allocation']
        before={t:c.execute(f'SELECT to_jsonb(t) FROM {t} t').fetchall() for t in tables}
        def constraints():
            return c.execute("""SELECT conrelid::regclass::text,pg_get_constraintdef(oid)
                FROM pg_constraint WHERE conrelid::regclass::text LIKE 'flow_v3_%' ORDER BY 1,2""").fetchall()
        old_constraints=constraints()
        migration=(ROOT/'database/migrations/20260913_flow_v3_live_routes.sql').read_text()
        c.execute(migration);c.execute(migration)
        c.execute((ROOT/'database/migrations/20260913_flow_v3_live_routes_down.sql').read_text())
        self.assertEqual(constraints(),old_constraints)
        c.execute(migration)
        for table,rows in before.items():
            cols=list(rows[0][0]) if rows else []
            after=c.execute(f'SELECT to_jsonb(t) FROM {table} t').fetchall()
            self.assertEqual(len(rows),len(after),table)
            self.assertEqual(sorted(json.dumps(r[0],sort_keys=True) for r in rows),
                sorted(json.dumps({k:r[0][k] for k in cols},sort_keys=True) for r in after),table)
        print('MIGRATION: original columns/15 capitals/57 orders/PAPER unchanged; reapply PASS')
        checkpoint_migration=(ROOT/'database/migrations/20260915_flow_v3_checkpoint_execution_code_check.sql').read_text()
        checkpoint_before=c.execute('SELECT to_jsonb(t) FROM flow_v3_live_checkpoint_allocation t').fetchall()
        c.execute(checkpoint_migration);c.execute(checkpoint_migration)
        self.assertEqual(checkpoint_before,c.execute('SELECT to_jsonb(t) FROM flow_v3_live_checkpoint_allocation t').fetchall())
        # Copy real CHECKs to a disposable table; no fake broker or LIVE owner
        # required to test the complete accepted/rejected product domain.
        c.execute('CREATE TEMP TABLE checkpoint_code_probe (LIKE flow_v3_live_checkpoint_allocation INCLUDING CONSTRAINTS)')
        for code in ('000660','005930','0193T0','0193W0','0197X0','0193L0'):
            c.execute("INSERT INTO checkpoint_code_probe(broker_order_id,live_trade_id,stock_code,side,delta_quantity,delta_amount,checkpoint_version) VALUES(%s,1,%s,'BUY',3,5104500,1)",(identity('probe'+code),code))
        with self.assertRaises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO checkpoint_code_probe(broker_order_id,live_trade_id,stock_code,side,delta_quantity,delta_amount,checkpoint_version) VALUES(%s,1,'999999','BUY',3,5104500,1)",(identity('probe-invalid'),))
        self.assertEqual(c.execute('SELECT count(*) FROM checkpoint_code_probe').fetchone()[0],6)
        c.autocommit=False
        class Pool:
            @contextmanager
            def connection(self):
                try: yield c;c.commit()
                except Exception: c.rollback();raise
        pool=Pool();admin=LiveOperations(pool);repo=LiveRepository(pool,lambda *a:None)
        a=admin.register('FV3008209','UNDERLYING','6000000','USER_TEST',now+timedelta(minutes=1),entry_enabled=True)
        admin.set_entry(a,True,now+timedelta(minutes=2))
        self.assertEqual(c.execute('SELECT entry_resume_at FROM flow_v3_strategy_operation WHERE operation_id=%s',(a,)).fetchone()[0],now+timedelta(minutes=1));c.commit()
        self.assertRaises(ValueError,admin.register,'FV3008209','UNDERLYING','1','DUPLICATE',now+timedelta(minutes=1))
        self.assertRaises(ValueError,admin.register,'NOT_IN_MASTER','UNDERLYING','1','TEST',now)
        self.assertRaises(ValueError,admin.register,'SAMSUNG_SHORT','UNDERLYING','1','TEST',now)
        sam=admin.register('SAMSUNG_LONG','UNDERLYING','7000000','USER_TEST',now+timedelta(minutes=1),entry_enabled=True)
        lev=admin.register('SAMSUNG_LONG','LEVERAGE','1234567','USER_TEST',now+timedelta(minutes=1),entry_enabled=True)
        inv=admin.register('SAMSUNG_SHORT','INVERSE','7654321','USER_TEST',now+timedelta(minutes=1),entry_enabled=True)
        t=now+timedelta(minutes=2)
        e=event('FV3008209',t);se=event('SAMSUNG_LONG',t);ie=event('SAMSUNG_SHORT',t);c.commit()
        quotes={code:(Decimal(250000 if code in ('000660','005930') else 10000),t+timedelta(minutes=1)) for code in ROUTES.values()}
        repo.cycle(t+timedelta(minutes=1),quotes)
        self.assertEqual(c.execute('SELECT count(*) FROM flow_v3_live_intent WHERE event_id=%s',(e,)).fetchone()[0],2)
        self.assertEqual(c.execute('SELECT quantity FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s',(a,e)).fetchone()[0],24)
        self.assertEqual(c.execute("SELECT count(*) FROM flow_v3_live_intent WHERE status='READY_NO_SEND' AND event_id IN (%s,%s,%s)",(e,se,ie)).fetchone()[0],5)
        self.assertEqual(c.execute('SELECT sum(post_attempt_count) FROM flow_v3_live_order').fetchone()[0],0);c.commit()
        # Broker facts below are injected directly into the disposable fixture only.
        def fill(op,event_id,side,price,num):
            row=c.execute('''SELECT o.broker_order_id,i.quantity,i.execution_code,i.execution_not_before FROM flow_v3_live_order o
                JOIN flow_v3_live_intent i USING(intent_id) WHERE i.operation_id=%s AND i.event_id=%s AND i.side=%s''',(op,event_id,side)).fetchone();c.commit()
            oid,qty,code,tm=row
            repo.record_response(oid,{'rt_cd':'0','output':{'ODNO':num}},tm)
            return repo.observe(oid,order_number=num,order_date=tm.date(),stock_code=code,side=side,requested_quantity=qty,
                filled_quantity=qty,filled_amount=Decimal(price)*qty,status='FILLED',observed_at=tm,remaining_quantity=0)['live_trade_id']
        legacy=c.execute("SELECT operation_id FROM flow_v3_live_capital WHERE strategy_id='FV3008209' AND operation_id<>%s",(a,)).fetchone()[0];c.commit()
        tid=fill(a,e,'BUY',250000,'TEST_BUY_A');stid=fill(sam,se,'BUY',250000,'TEST_BUY_B')
        ltid=fill(legacy,e,'BUY',10000,'TEST_BUY_LEGACY')
        self.assertNotEqual(tid,ltid)
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with c.transaction():
                c.execute('UPDATE flow_v3_live_lot SET operation_id=%s WHERE live_trade_id=%s',(sam,tid))
        admin.set_entry(a,False,t+timedelta(minutes=2))
        stopped=event('FV3008209',t+timedelta(minutes=3));c.commit()
        c.execute("INSERT INTO flow_v3_minute_state VALUES('000660',%s,true,'{\"01\":-1}','{}')",(t+timedelta(minutes=3),));c.commit()
        at=t+timedelta(minutes=4);repo.cycle(at,{k:(v[0],at) for k,v in quotes.items()})
        self.assertEqual(c.execute('SELECT count(*) FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s',(a,stopped)).fetchone()[0],0);c.commit()
        # Confirmation after EXIT requested is required before selling.
        bo=c.execute("SELECT o.broker_order_id,i.quantity FROM flow_v3_live_order o JOIN flow_v3_live_intent i USING(intent_id) WHERE i.operation_id=%s AND i.side='BUY'",(a,)).fetchone();c.commit()
        repo.observe(bo[0],order_number='TEST_BUY_A',order_date=at.date(),stock_code='000660',side='BUY',
            requested_quantity=bo[1],filled_quantity=bo[1],filled_amount=Decimal(250000)*bo[1],status='FILLED',observed_at=at,remaining_quantity=0)
        repo.cycle(at,{k:(v[0],at) for k,v in quotes.items()});fill(a,e,'SELL',275000,'TEST_SELL_A')
        c.execute("INSERT INTO broker_shared_cost_snapshot VALUES(%s,'000660','FINALIZED_BY_STABLE_RECHECK')",(at.date(),))
        for side,amount in [('BUY',6000000),('SELL',6600000)]:
            c.execute("INSERT INTO broker_shared_cost_allocation VALUES(%s,'000660','FLOW',%s,%s,%s,0,0,0,0)",(at.date(),tid,side,amount))
        c.commit();repo.cycle(at,{k:(v[0],at) for k,v in quotes.items()})
        self.assertEqual(c.execute('SELECT current_capital FROM flow_v3_live_capital WHERE operation_id=%s',(a,)).fetchone()[0],6600000)
        self.assertEqual(c.execute('SELECT current_capital FROM flow_v3_live_capital WHERE operation_id=%s',(sam,)).fetchone()[0],7000000)
        self.assertEqual(c.execute("SELECT current_capital FROM flow_v3_live_capital WHERE strategy_id='FV3008209' AND operation_id<>%s",(a,)).fetchone()[0],Decimal('16732.5'));c.commit()
        # Resume advances the no-replay boundary, and a new entry sizes from realized capital.
        admin.set_entry(a,True,at+timedelta(minutes=1));new=event('FV3008209',at+timedelta(minutes=2));c.commit()
        at+=timedelta(minutes=3);repo.cycle(at,{k:(v[0],at) for k,v in quotes.items()})
        self.assertEqual(c.execute('SELECT quantity FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s',(a,new)).fetchone()[0],26)
        self.assertEqual(c.execute("SELECT count(*) FROM flow_v3_live_order WHERE status='READY_NO_SEND' AND request_payload='{}'").fetchone()[0],57)
        self.assertEqual(c.execute('SELECT count(*) FROM flow_v3_live_capital WHERE current_capital<>initial_capital+realized_net').fetchone()[0],0)
        snapshot=c.execute('SELECT operation_id,current_capital FROM flow_v3_live_capital ORDER BY operation_id').fetchall();c.commit()
        repo=LiveRepository(pool,lambda *a:None);repo.cycle(at,{k:(v[0],at) for k,v in quotes.items()})
        self.assertEqual(snapshot,c.execute('SELECT operation_id,current_capital FROM flow_v3_live_capital ORDER BY operation_id').fetchall());c.commit()
        # New event while an existing lot is OPEN remains independent, same operation.
        ntid=fill(a,new,'BUY',250000,'TEST_BUY_A2')
        another=event('FV3008209',at+timedelta(minutes=1));c.commit()
        later=at+timedelta(minutes=2)
        repo.cycle(later,{k:(v[0],later) for k,v in quotes.items()})
        self.assertEqual(c.execute('SELECT count(*) FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s',(a,another)).fetchone()[0],1);c.commit()
        # Ending a route blocks future entries but preserves that route's EXIT.
        admin.set_entry(a,False,later,end=True)
        sig=later+timedelta(minutes=1);endtime=sig+timedelta(minutes=1)
        c.execute("INSERT INTO flow_v3_minute_state VALUES('000660',%s,true,'{\"01\":-1}','{}')",(sig,));c.commit()
        repo.cycle(endtime,{k:(v[0],endtime) for k,v in quotes.items()})
        bo=c.execute("SELECT o.broker_order_id,i.quantity FROM flow_v3_live_order o JOIN flow_v3_live_intent i USING(intent_id) WHERE i.operation_id=%s AND i.event_id=%s AND i.side='BUY'",(a,new)).fetchone();c.commit()
        repo.observe(bo[0],order_number='TEST_BUY_A2',order_date=endtime.date(),stock_code='000660',side='BUY',
            requested_quantity=26,filled_quantity=26,filled_amount=Decimal(250000)*26,status='FILLED',observed_at=endtime,remaining_quantity=0)
        repo.cycle(endtime,{k:(v[0],endtime) for k,v in quotes.items()});fill(a,new,'SELL',225000,'TEST_SELL_A2')
        for side,amount in [('BUY',6500000),('SELL',5850000)]:
            c.execute("INSERT INTO broker_shared_cost_allocation VALUES(%s,'000660','FLOW',%s,%s,%s,0,0,0,0)",(endtime.date(),ntid,side,amount))
        c.commit();repo.cycle(endtime,{k:(v[0],endtime) for k,v in quotes.items()})
        self.assertEqual(c.execute('SELECT current_capital FROM flow_v3_live_capital WHERE operation_id=%s',(a,)).fetchone()[0],5950000)
        self.assertEqual(c.execute('SELECT current_capital FROM flow_v3_live_capital WHERE operation_id=%s',(legacy,)).fetchone()[0],Decimal('16732.5'));c.commit()
        # New epoch uses explicit capital, never resets the old epoch.
        a2=admin.register('FV3008209','UNDERLYING','8000000','NEW_EPOCH_TEST',endtime+timedelta(minutes=1),entry_enabled=True)
        blocked=event('FV3008209',endtime+timedelta(minutes=2));c.commit()
        bt=endtime+timedelta(minutes=3)
        repo.cash_check=lambda *args:'KIS_ORDERABLE_CASH_INSUFFICIENT'
        repo.cycle(bt,{k:(v[0],bt) for k,v in quotes.items()})
        repo.cash_check=lambda *args:None
        repo.cycle(bt,{k:(v[0],bt) for k,v in quotes.items()})
        self.assertEqual(c.execute('SELECT status,reason FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s',(a2,blocked)).fetchone(),('BLOCKED','KIS_ORDERABLE_CASH_INSUFFICIENT'))
        self.assertEqual(c.execute('SELECT current_capital FROM flow_v3_live_capital WHERE operation_id=%s',(a,)).fetchone()[0],5950000);c.commit()
        rejected=event('FV3008209',bt+timedelta(minutes=1));c.commit();bt+=timedelta(minutes=2)
        repo.cycle(bt,{k:(v[0],bt) for k,v in quotes.items()})
        oid=c.execute("SELECT o.broker_order_id FROM flow_v3_live_order o JOIN flow_v3_live_intent i USING(intent_id) WHERE i.operation_id=%s AND i.event_id=%s",(a2,rejected)).fetchone()[0];c.commit()
        repo.record_response(oid,{'rt_cd':'1','msg_cd':'TEST_REJECT','msg1':'fixture insufficient'},bt)
        repo.cycle(bt,{k:(v[0],bt) for k,v in quotes.items()})
        self.assertEqual(c.execute('SELECT status FROM flow_v3_live_order WHERE broker_order_id=%s',(oid,)).fetchone()[0],'REJECTED')
        self.assertEqual(c.execute('SELECT count(*) FROM flow_v3_live_order').fetchone()[0],c.execute('SELECT count(DISTINCT intent_id) FROM flow_v3_live_order').fetchone()[0]);c.commit()
        # Preserve partial-entry cancel/fill races on operation-owned lots.
        for n,(initial,final) in enumerate(((3,3),(3,4),(5,5),(0,0))):
            sid=list(WHITELIST)[n]
            start=now+timedelta(hours=1,minutes=n*5)
            op=admin.register(sid,'UNDERLYING','1250000','PARTIAL_FIXTURE',start,entry_enabled=True)
            ev=event(sid,start);c.commit()
            obs=start+timedelta(minutes=1)
            repo.cycle(obs,{k:(v[0],obs) for k,v in quotes.items()})
            oid,iid=c.execute("SELECT o.broker_order_id,i.intent_id FROM flow_v3_live_order o JOIN flow_v3_live_intent i USING(intent_id) WHERE i.operation_id=%s AND i.event_id=%s",(op,ev)).fetchone();c.commit()
            number='PARTIAL'+str(n)
            repo.record_response(oid,{'rt_cd':'0','output':{'ODNO':number}},obs)
            repo.observe(oid,order_number=number,order_date=obs.date(),stock_code='000660',side='BUY',
                requested_quantity=5,filled_quantity=initial,filled_amount=Decimal(initial*250000),
                status='FILLED' if initial==5 else 'PARTIAL',observed_at=obs,remaining_quantity=5-initial)
            ex=start+timedelta(minutes=2);obs=ex+timedelta(minutes=1)
            c.execute("INSERT INTO flow_v3_minute_state VALUES('000660',%s,true,'{\"01\":-1}','{}')",(ex,));c.commit()
            repo.cycle(obs,{k:(v[0],obs) for k,v in quotes.items()})
            if initial<5:
                repo.prepare_cancel(iid,number=number,branch='FIXTURE',cancellable_quantity=5-initial,observed_at=obs)
                repo.cancel_response(iid,{'rt_cd':'0','output':{'ODNO':'C'+number}},obs)
            repo=LiveRepository(pool,lambda *a:None)  # Restart over the same durable state.
            repo.observe(oid,order_number=number,order_date=obs.date(),stock_code='000660',side='BUY',
                requested_quantity=5,filled_quantity=final,filled_amount=Decimal(final*250000),
                status='FILLED' if final==5 else 'CANCELLED',observed_at=obs+timedelta(seconds=1),remaining_quantity=0)
            obs+=timedelta(seconds=2)
            repo.cycle(obs,{k:(v[0],obs) for k,v in quotes.items()});repo.cycle(obs,{k:(v[0],obs) for k,v in quotes.items()})
            sells=c.execute("SELECT quantity FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s AND side='SELL'",(op,ev)).fetchall();c.commit()
            self.assertEqual(sells,[(final,)] if final else [])
            if final:
                ptid=fill(op,ev,'SELL',251000,'PARTIAL_SELL'+str(n))
                self.assertEqual(c.execute('SELECT bought_quantity-sold_quantity FROM flow_v3_live_lot WHERE live_trade_id=%s',(ptid,)).fetchone()[0],0);c.commit()
        # Real PostgreSQL transport queries, fake clock and fake HTTP only.
        c.execute("UPDATE flow_v3_send_profile SET enabled='Y',updated_at=clock_timestamp()-interval '1 minute'");c.commit()
        post_time=now+timedelta(hours=4)
        with patch.dict(os.environ,{'FLOW_V3_ACTUAL_SEND':'Y'}):
            pe=event('FV3008209',post_time-timedelta(minutes=1));c.commit()
            repo.cycle(post_time,{k:(v[0],post_time) for k,v in quotes.items()})
            class ClockConnection:
                def transaction(self): return c.transaction()
                def execute(self,sql,args=None):
                    return c.execute(sql.replace("clock_timestamp() AT TIME ZONE 'Asia/Seoul'","timestamp '2026-09-13 13:00:00'").replace('localtimestamp',"timestamp '2026-09-13 13:00:00'")
                        .replace('current_date',"date '2026-09-13'").replace('localtime',"time '13:00:00'"),args)
            class ClockPool:
                @contextmanager
                def connection(self):
                    try: yield ClockConnection();c.commit()
                    except Exception: c.rollback();raise
            calls=[]
            def fake_post(**kw):
                calls.append(kw);return {'rt_cd':'1','msg_cd':'FIXTURE_REJECT'}
            client=SimpleNamespace(post_once=fake_post,get=lambda **kw:{'output':{'nrcvb_buy_amt':'100000000'}})
            transport=FlowTransport(LiveRepository(ClockPool()),client,SimpleNamespace(cano='TEST',account_product_code='01'))
            self.assertEqual(transport.run(),2)  # Same signal -> underlying + legacy leverage.
            self.assertEqual({x['payload']['PDNO'] for x in calls},{'000660','0193T0'})
            self.assertEqual(transport.run(),0)
            self.assertEqual(c.execute("SELECT count(*) FROM flow_v3_live_order WHERE status='READY_NO_SEND' AND request_payload='{}' AND NOT send_enabled AND post_attempt_count=0").fetchone()[0],57);c.commit()
        self.assertEqual(c.execute('SELECT count(*) FROM flow_v3_live_lot WHERE bought_quantity<sold_quantity').fetchone()[0],0)
        self.assertEqual(c.execute('SELECT count(*) FROM flow_v3_live_capital WHERE current_capital<>initial_capital+realized_net').fetchone()[0],0);c.commit()
        # Rollback must refuse to erase new route history.
        with self.assertRaises(psycopg.errors.RaiseException):
            c.execute((ROOT/'database/migrations/20260913_flow_v3_live_routes_down.sql').read_text())
        c.rollback()
        c.execute((ROOT/'scripts/ops/verify_flow_v3_live_routes_readonly.sql').read_text());c.commit()
        from src.flow_v3.live_preparation import record_preparations
        record_preparations(pool)  # SQL planning against migrated schema, never an order.
        from test.flow_v3_operating_fixture import verify
        verify(self,c,pool,event,quotes)
        print('ROUTES: rollback/fanout/lot ownership/profit+loss/ended EXIT/restart/no replay/57 preservation PASS')


if __name__=='__main__': unittest.main()
