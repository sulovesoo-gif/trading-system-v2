"""Invoked only by the guarded localhost:55491 disposable PostgreSQL test."""
from contextlib import contextmanager
from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from src.flow_v3.live_operations import LiveOperations
from src.flow_v3.live_repository import LiveRepository
from src.flow_v3.live_transport import FlowTransport
from src.service.flow_v3_dashboard_service import _live_operations


def verify(case,c,pool,event,quotes):
    root=Path(__file__).resolve().parents[1]
    c.commit();c.autocommit=True
    before=c.execute('SELECT to_jsonb(c) FROM flow_v3_live_capital c ORDER BY operation_id').fetchall()
    paper=c.execute('SELECT to_jsonb(t) FROM flow_v3_paper_trade t ORDER BY paper_trade_id').fetchall()
    migration=(root/'database/migrations/20260914_flow_v3_capital_change.sql').read_text()
    c.execute(migration);c.execute(migration)
    case.assertEqual(before,c.execute('SELECT to_jsonb(c) FROM flow_v3_live_capital c ORDER BY operation_id').fetchall())
    c.autocommit=False
    admin=LiveOperations(pool);repo=LiveRepository(pool,lambda *a:None)
    sid='CAPITAL_TEST';start=datetime(2026,9,13,14)
    c.execute("INSERT INTO flow_v3_strategy_master VALUES(%s,'000660','LONG','0193T0','Y')",(sid,));c.commit()
    op=admin.set_capital(sid,'UNDERLYING','6000000','NEW',start)['operation_id']
    lev=admin.set_capital(sid,'LEVERAGE','20000','LEV',start)['operation_id']
    ev=event(sid,start+timedelta(minutes=1));c.commit()
    def cycle(at): return repo.cycle(at,{k:(v[0],at) for k,v in quotes.items()})
    cycle(start+timedelta(minutes=2))
    def order(op,ev,side):
        r=c.execute("""SELECT o.broker_order_id,i.quantity,i.execution_code FROM flow_v3_live_order o
            JOIN flow_v3_live_intent i USING(intent_id) WHERE i.operation_id=%s AND i.event_id=%s AND i.side=%s""",(op,ev,side)).fetchone();c.commit();return r
    oid,qty,code=order(op,ev,'BUY')
    case.assertEqual(qty,24)
    obs=start+timedelta(minutes=2)
    repo.record_response(oid,{'rt_cd':'0','output':{'ODNO':'CAP_BUY'}},obs)
    number='CAP_BUY'
    def observe(at):
        return repo.observe(oid,order_number=number,order_date=at.date(),stock_code=code,side='BUY',
            requested_quantity=qty,filled_quantity=qty,filled_amount=Decimal(250000)*qty,
            status='FILLED',observed_at=at,remaining_quantity=0)
    tid=observe(obs)['live_trade_id']
    capital=c.execute('SELECT to_jsonb(c) FROM flow_v3_live_capital c WHERE operation_id=%s',(op,)).fetchone()[0];c.commit()
    stopped=start+timedelta(minutes=3)
    admin.set_capital(sid,'UNDERLYING','0','STOP',stopped)
    case.assertRaises(ValueError,admin.set_entry,op,True,stopped)
    past=event(sid,stopped+timedelta(minutes=1));c.commit()
    c.execute("INSERT INTO flow_v3_minute_state VALUES('000660',%s,true,'{\"01\":-1}','{}')",(stopped+timedelta(minutes=1),));c.commit()
    at=stopped+timedelta(minutes=2);cycle(at);observe(at);cycle(at)
    case.assertEqual(order(op,ev,'SELL')[1],24)  # Zero capital never disables EXIT.
    case.assertEqual(c.execute("SELECT count(*) FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s",(op,past)).fetchone()[0],0)
    case.assertEqual(c.execute('SELECT allocated_amount,entry_enabled FROM flow_v3_strategy_operation WHERE operation_id=%s',(lev,)).fetchone(),(Decimal(20000),True));c.commit()
    resumed=at+timedelta(minutes=1)
    new=admin.set_capital(sid,'UNDERLYING','7000000','RESUME',resumed)['operation_id']
    case.assertNotEqual(new,op)
    case.assertEqual(admin.set_capital(sid,'UNDERLYING','7000000','RESUME',resumed+timedelta(minutes=1))['operation_id'],new)
    case.assertRaises(ValueError,admin.set_capital,sid,'UNDERLYING','1','RESUME',resumed)
    fresh=event(sid,resumed+timedelta(minutes=1));c.commit();cycle(resumed+timedelta(minutes=2))
    case.assertEqual(order(new,fresh,'BUY')[1],28)
    case.assertEqual(c.execute('SELECT initial_capital,current_capital,realized_net FROM flow_v3_live_capital WHERE operation_id=%s',(op,)).fetchone(),
        (Decimal(capital['initial_capital']),Decimal(capital['current_capital']),Decimal(capital['realized_net'])))
    case.assertEqual(c.execute('SELECT count(*) FROM flow_v3_live_intent WHERE operation_id=%s AND event_id=%s',(new,past)).fetchone()[0],0)
    case.assertEqual(c.execute('SELECT count(*) FROM flow_v3_live_capital_change WHERE strategy_id=%s',(sid,)).fetchone()[0],4);c.commit()
    with c.cursor() as q:
        rows=_live_operations(q,sid)
    case.assertEqual([r['execution_route'] for r in rows],['UNDERLYING','LEVERAGE'])
    case.assertTrue(all(r['profit_per_trade'] is None for r in rows));c.commit()

    # EOD signal timestamp is the completed 15:18 BAR, released at 15:19 wall time.
    # At 15:18 wall time that bar is still forming, so it must NOT be used.
    eodsid='EOD_TEST';c.execute("INSERT INTO flow_v3_strategy_master VALUES(%s,'000660','LONG','0193T0','Y')",(eodsid,));c.commit()
    op=admin.set_capital(eodsid,'UNDERLYING','1250000','EOD',datetime(2026,9,13,15,15))['operation_id']
    ev=event(eodsid,datetime(2026,9,13,15,16))
    c.execute("UPDATE flow_v3_runtime_entry_event SET exit_policy_code='SIGNAL_EOD' WHERE event_id=%s",(ev,));c.commit()
    with patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':'Y'}): cycle(datetime(2026,9,13,15,17))
    oid,qty,code=order(op,ev,'BUY');case.assertEqual(qty,5)
    number='EOD_BUY'
    repo.record_response(oid,{'rt_cd':'0','output':{'ODNO':number}},datetime(2026,9,13,15,17))
    observe(datetime(2026,9,13,15,17))
    cycle(datetime(2026,9,13,15,18))
    case.assertIsNone(order(op,ev,'SELL'))
    c.execute("INSERT INTO flow_v3_minute_state VALUES('000660','2026-09-13 15:18',true,'{}','{}')")
    c.execute("INSERT INTO flow_v3_runtime_cursor VALUES('000660','2026-09-13 15:18')");c.commit()
    admin.set_capital(eodsid,'UNDERLYING','0','EOD_PAUSE',datetime(2026,9,13,15,18,30))
    clock=[datetime(2026,9,13,15,19,1)]
    with patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':'Y'}):
        cycle(clock[0]);case.assertIsNone(order(op,ev,'SELL'))
        observe(clock[0]);cycle(clock[0])
    case.assertEqual(order(op,ev,'SELL')[1],5)
    case.assertEqual(c.execute("SELECT signal_time,execution_not_before FROM flow_v3_live_intent WHERE operation_id=%s AND side='SELL'",(op,)).fetchone(),
        (datetime(2026,9,13,15,18),datetime(2026,9,13,15,19)));c.commit()
    class ClockConnection:
        def transaction(self): return c.transaction()
        def execute(self,sql,args=None):
            stamp=clock[0].isoformat(' ')
            sql=sql.replace("clock_timestamp() AT TIME ZONE 'Asia/Seoul'",f"timestamp '{stamp}'")
            return c.execute(sql.replace('localtimestamp',f"timestamp '{stamp}'")
                .replace('current_date',"date '2026-09-13'").replace('localtime',f"time '{clock[0].time()}'"),args)
    class ClockPool:
        @contextmanager
        def connection(self):
            try: yield ClockConnection();c.commit()
            except Exception: c.rollback();raise
    calls=[]
    client=SimpleNamespace(post_once=lambda **kw:(calls.append(kw) or {'rt_cd':'1','msg_cd':'TEST_ONLY'}),
                           get=lambda **kw:{'output':{'nrcvb_buy_amt':'100000000'}})
    transport=FlowTransport(LiveRepository(ClockPool()),client,SimpleNamespace(cano='FAKE',account_product_code='01'))
    with patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':'N'}): case.assertEqual(transport.run(exits_only=True),0)
    # A claim at/after 15:20 is denied; the same fixture at 15:19 can claim once.
    clock[0]=datetime(2026,9,13,15,20)
    with patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':'Y'}): case.assertEqual(transport.run(exits_only=True),0)
    clock[0]=datetime(2026,9,13,15,19,2)
    with patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':'Y'}):
        case.assertEqual(transport.run(exits_only=True),1)
        case.assertEqual(transport.run(exits_only=True),0)
    case.assertEqual(len(calls),1)
    case.assertEqual(calls[0]['payload']['ORD_QTY'],'5')
    case.assertEqual(c.execute("SELECT to_jsonb(t) FROM flow_v3_paper_trade t ORDER BY paper_trade_id").fetchall()[:len(paper)],paper)
    case.assertEqual(c.execute("SELECT count(*) FROM flow_v3_live_order WHERE status='READY_NO_SEND' AND request_payload='{}' AND NOT send_enabled AND post_attempt_count=0").fetchone()[0],57)
    case.assertEqual(c.execute('SELECT count(*) FROM flow_v3_live_capital WHERE current_capital<>initial_capital+realized_net').fetchone()[0],0);c.commit()
    # Minimal legacy fixture omitted this real PAPER column; add it in TEST DB
    # only to plan/execute the shipped read-only deployment verification SQL.
    c.execute("ALTER TABLE flow_v3_paper_trade ADD COLUMN trade_status text DEFAULT 'CLOSED'");c.commit()
    c.autocommit=True
    c.execute((root/'scripts/ops/verify_flow_v3_operating_contract_readonly.sql').read_text())
    c.autocommit=False
    print('CAPITAL/EOD: audit reapply, zero BUY block/SELL retained, epoch/no replay, route isolation, 15:19 claim/15:20 deny, 57 history PASS')
