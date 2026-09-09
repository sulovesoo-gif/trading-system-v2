"""FLOW integration fixtures live ONLY in pg_temp; no real orders or source writes."""
import sys
from contextlib import contextmanager
from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
import psycopg
from src.repository.database import DatabaseSettings
from src.flow_v3.live_repository import LiveRepository
from src.flow_v3.live_contract import WHITELIST,LONG_IDS


def main(migration=None):
    load_dotenv(ROOT/'.env')
    c=psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs(),autocommit=True)
    try:
        c.execute('SET search_path TO pg_temp,public')
        for table,pk in [('flow_v3_live_trade','live_trade_id'),('flow_v3_strategy_operation','operation_id')]:
            c.execute(f'CREATE TEMP TABLE {table}(LIKE public.{table} INCLUDING ALL)')
            c.execute(f'CREATE TEMP SEQUENCE {pk}_fixture_seq')
            c.execute(f"ALTER TABLE {table} ALTER COLUMN {pk} SET DEFAULT nextval('pg_temp.{pk}_fixture_seq')")
        c.execute('CREATE TEMP TABLE flow_v3_strategy_master(strategy_id text PRIMARY KEY,stock_code text,direction text,execution_code text)')
        c.execute('CREATE TEMP TABLE flow_v3_live_preparation(strategy_id varchar(20) PRIMARY KEY,execution_code text,current_capital numeric)')
        c.execute('CREATE TEMP TABLE flow_v3_paper_trade(paper_trade_id bigint PRIMARY KEY)')
        c.execute('''CREATE TEMP TABLE raw_stock_daily(stock_code text,trade_date date,trading_venue text,
            collect_cycle text,data_source text,close_price numeric,collected_at timestamp)''')
        c.execute('''CREATE TEMP TABLE flow_v3_runtime_entry_event(event_id bigint PRIMARY KEY,strategy_id text,
            entry_event_key text,paper_trade_id bigint,entry_signal_time timestamp,created_at timestamp,
            stock_code text,direction text,execution_code text,exit_policy_code text,entry_family_code text,
            exit_fast_period int,exit_slow_period int)''')
        c.execute('''CREATE TEMP TABLE flow_v3_minute_state(stock_code text,bar_time timestamp,is_complete boolean,
            flow_crosses jsonb,velocity_crosses jsonb)''')
        c.execute('CREATE TEMP TABLE flow_v3_runtime_cursor(stock_code text,last_bar_time timestamp)')
        c.execute('CREATE TEMP TABLE flow_v3_live_checkpoint_allocation (LIKE public.flow_v3_live_checkpoint_allocation INCLUDING ALL)')
        for table in ('broker_shared_cost_snapshot','broker_shared_cost_allocation'):
            c.execute(f'CREATE TEMP TABLE {table}(LIKE public.{table} INCLUDING ALL)')
        sql=migration or (ROOT/'database/migrations/20260909_flow_v3_live_pipeline.sql').read_text()
        c.execute(sql.replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE IF NOT EXISTS'))
        approval_sql=(ROOT/'database/migrations/20260910_flow_v3_send_authorization.sql').read_text()
        c.execute(approval_sql.replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE IF NOT EXISTS'))
        c.execute(approval_sql.replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE IF NOT EXISTS'))
        for table in ('flow_v3_live_capital','flow_v3_live_intent','flow_v3_live_order','flow_v3_live_trade',
                      'flow_v3_strategy_operation','raw_stock_daily','flow_v3_live_checkpoint_allocation',
                      'flow_v3_send_profile','flow_v3_live_entry_release','flow_v3_live_worker_status'):
            assert c.execute("SELECT relpersistence FROM pg_class WHERE oid=%s::regclass",(table,)).fetchone()[0]=='t'
        for sid,(direction,code) in WHITELIST.items():
            c.execute('INSERT INTO flow_v3_strategy_master VALUES(%s,%s,%s,%s)',(sid,'000660',direction,code))
            c.execute('INSERT INTO flow_v3_live_preparation(strategy_id,execution_code,current_capital) VALUES(%s,%s,150)',(sid,code))
        for code in ('0193T0','0197X0'):
            c.execute("INSERT INTO raw_stock_daily VALUES(%s,'2026-09-08','KRX','DAILY','KIS',100,'2026-09-08 16:00')",(code,))
        c.autocommit=False
        class Pool:
            @contextmanager
            def connection(self):
                try:
                    yield c
                    c.commit()
                except Exception:
                    c.rollback();raise
        repo=LiveRepository(Pool());now=datetime(2026,9,9,9)
        repo.activate(now);repo.activate(now)
        assert c.execute('SELECT count(*) FROM flow_v3_live_capital').fetchone()[0]==15
        assert c.execute('SELECT count(*) FROM flow_v3_strategy_operation').fetchone()[0]==15
        c.commit()
        def event(eid,sid,t,policy='SIGNAL_HOLD'):
            direction,code=WHITELIST[sid]
            c.execute('INSERT INTO flow_v3_paper_trade VALUES(%s)',(eid,))
            c.execute('INSERT INTO flow_v3_runtime_entry_event VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,3)',
                (eid,sid,'KEY'+str(eid),eid,t,t,'000660',direction,code,policy,'F1'))
            c.commit()
        def cycle(t):
            return repo.cycle(t,{'0193T0':(Decimal(100),t),'0197X0':(Decimal(100),t)})
        def confirm_full(t):
            for o in repo.orders_to_poll():
                if o['status']=='FILLED':
                    repo.observe(o['broker_order_id'],order_number=o['broker_order_number'],
                        order_date=o['broker_order_date'],stock_code=o['execution_code'],side=o['side'],
                        requested_quantity=o['quantity'],filled_quantity=o['cumulative_quantity'],
                        filled_amount=o['cumulative_amount'],status='FILLED',observed_at=t,remaining_quantity=0)
        def fill(iid,num,qty=1,amount=100):
            row=c.execute('SELECT broker_order_id,i.execution_code,i.side,i.quantity,i.execution_not_before FROM flow_v3_live_order o JOIN flow_v3_live_intent i USING(intent_id) WHERE i.intent_id=%s',(iid,)).fetchone();c.commit()
            oid,code,side,requested,observed=row
            repo.record_response(oid,{'rt_cd':'0','output':{'ODNO':num}},observed)
            r=repo.observe(oid,order_number=num,order_date=now.date(),stock_code=code,side=side,
                requested_quantity=requested,filled_quantity=qty,filled_amount=Decimal(amount),
                status='FILLED' if qty==requested else 'PARTIAL',observed_at=observed)
            assert repo.observe(oid,order_number=num,order_date=now.date(),stock_code=code,side=side,
                requested_quantity=requested,filled_quantity=qty,filled_amount=Decimal(amount),
                status='FILLED' if qty==requested else 'PARTIAL',observed_at=observed)['delta']==0
            return r['live_trade_id']
        t=datetime(2026,9,9,10)
        # Same strategy NEW events while existing lot OPEN, and another strategy same signal.
        event(1,LONG_IDS[0],t);cycle(t+timedelta(minutes=1))
        iid=c.execute("SELECT intent_id FROM flow_v3_live_intent WHERE event_id=1").fetchone()[0];c.commit()
        fill(iid,'BUY1')
        event(2,LONG_IDS[0],t+timedelta(minutes=1));event(3,LONG_IDS[1],t+timedelta(minutes=1))
        cycle(t+timedelta(minutes=2));cycle(t+timedelta(minutes=2))
        for eid in (2,3):
            iid=c.execute('SELECT intent_id FROM flow_v3_live_intent WHERE event_id=%s',(eid,)).fetchone()[0];c.commit();fill(iid,'BUY'+str(eid))
        assert c.execute('SELECT count(*) FROM flow_v3_live_order').fetchone()[0]==3
        assert c.execute('SELECT count(*) FROM flow_v3_live_lot WHERE strategy_id=%s',(LONG_IDS[0],)).fetchone()[0]==2
        c.execute("INSERT INTO flow_v3_minute_state VALUES('000660','2026-09-09 10:03',true,'{\"01\":-1}','{}')");c.commit()
        cycle(t+timedelta(minutes=4));confirm_full(t+timedelta(minutes=4));cycle(t+timedelta(minutes=4))
        exits=c.execute("SELECT intent_id,event_id FROM flow_v3_live_intent WHERE side='SELL' ORDER BY event_id").fetchall();c.commit()
        assert len(exits)==3
        for iid,eid in exits: fill(iid,'SELL'+str(eid),amount=160)
        assert c.execute("SELECT count(*) FROM flow_v3_live_lot WHERE exposure_status='FILLED_COST_PENDING'").fetchone()[0]==3
        assert c.execute('SELECT sum(current_capital-initial_capital) FROM flow_v3_live_capital').fetchone()[0]==0
        c.commit()
        # Broker fees are fixtures, not copied from PAPER. Coverage of both sides required.
        c.execute("""INSERT INTO broker_shared_cost_snapshot
            (trade_date,execution_stock_code,buy_fee,sell_fee,sell_tax,other_cost,broker_snapshot_at,status,
             fingerprint,confirmation_count,last_confirmed_at)
            VALUES('2026-09-09','0193T0',3,3,0,0,'2026-09-10 10:00','FINALIZED_BY_STABLE_RECHECK','fixture',2,'2026-09-10 10:00')""")
        c.execute("""INSERT INTO broker_shared_cost_allocation
            (trade_date,execution_stock_code,family,live_trade_id,side,fill_notional,buy_fee,sell_fee,sell_tax,other_cost)
            SELECT broker_trade_date,'0193T0','FLOW',live_trade_id,side,sum(delta_amount),
                CASE side WHEN 'BUY' THEN 1 ELSE 0 END,CASE side WHEN 'SELL' THEN 1 ELSE 0 END,0,0
            FROM flow_v3_live_fill_checkpoint GROUP BY broker_trade_date,live_trade_id,side""");c.commit()
        assert cycle(t+timedelta(minutes=5))['settlements']==3
        before=c.execute('SELECT strategy_id,current_capital FROM flow_v3_live_capital ORDER BY 1').fetchall();c.commit()
        repo=LiveRepository(Pool())  # Repository/service restart equivalent over durable DB.
        assert cycle(t+timedelta(minutes=5))['settlements']==0
        assert before==c.execute('SELECT strategy_id,current_capital FROM flow_v3_live_capital ORDER BY 1').fetchall();c.commit()
        event(4,LONG_IDS[0],t+timedelta(minutes=5));cycle(t+timedelta(minutes=6))
        assert c.execute("SELECT quantity FROM flow_v3_live_intent WHERE event_id=4").fetchone()[0]==2
        c.commit()
        # EOD and cutoff (HOLD also rejects a 15:19 entry).
        event(5,LONG_IDS[2],datetime(2026,9,9,15,17),'SIGNAL_EOD')
        cycle(datetime(2026,9,9,15,18))
        iid=c.execute('SELECT intent_id FROM flow_v3_live_intent WHERE event_id=5').fetchone()[0];c.commit();fill(iid,'BUYEOD')
        c.execute("INSERT INTO flow_v3_runtime_cursor VALUES('000660','2026-09-09 15:18')");c.commit()
        cycle(datetime(2026,9,9,15,19))
        assert c.execute("SELECT count(*) FROM flow_v3_live_intent WHERE exit_reason='SIGNAL_EOD'").fetchone()[0]==1
        c.commit()
        event(6,LONG_IDS[0],datetime(2026,9,9,15,19))
        cycle(datetime(2026,9,9,15,19))
        assert c.execute('SELECT reason FROM flow_v3_live_intent WHERE event_id=6').fetchone()[0]=='ENTRY_AFTER_EOD_CUTOFF'
        assert c.execute('SELECT sum(post_attempt_count) FROM flow_v3_live_order').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM flow_v3_live_trade WHERE paper_trade_id IS NULL').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM flow_v3_live_capital WHERE current_capital<>initial_capital+realized_net').fetchone()[0]==0
        c.commit()
        # A partial entry is exposed; exit cannot incorrectly sell the requested
        # (rather than filled) quantity or close before a terminal broker fact.
        iid=c.execute('SELECT intent_id FROM flow_v3_live_intent WHERE event_id=4').fetchone()[0];c.commit()
        tid=fill(iid,'BUYPARTIAL',qty=1,amount=100)
        c.execute("INSERT INTO flow_v3_minute_state VALUES('000660','2026-09-09 10:07',true,'{\"01\":-1}','{}')");c.commit()
        cycle(datetime(2026,9,9,10,8))
        assert c.execute("SELECT reason FROM flow_v3_live_intent WHERE live_trade_id=%s AND side='SELL'",(tid,)).fetchone()[0]=='ENTRY_CANCEL_FINAL_HISTORY_PENDING'
        c.commit()
        for index,(initial,final) in enumerate(((3,3),(3,4),(5,5),(0,0))):
            sid=LONG_IDS[3+index];eid=100+index;start=datetime(2026,9,9,11,index*5)
            c.execute('UPDATE flow_v3_live_capital SET initial_close=350,initial_capital=525,current_capital=525 WHERE strategy_id=%s',(sid,));c.commit()
            event(eid,sid,start);cycle(start+timedelta(minutes=1))
            iid=c.execute('SELECT intent_id FROM flow_v3_live_intent WHERE event_id=%s',(eid,)).fetchone()[0];c.commit()
            fill(iid,'CASE'+str(eid),initial,initial*100)
            exit_at=start+timedelta(minutes=2);observed=exit_at+timedelta(minutes=1)
            c.execute("INSERT INTO flow_v3_minute_state VALUES('000660',%s,true,'{\"01\":-1}','{}')",(exit_at,));c.commit()
            cycle(observed)
            if initial<5:
                repo.prepare_cancel(iid,number='CASE'+str(eid),branch='06010',cancellable_quantity=5-initial,observed_at=observed)
                repo.cancel_response(iid,{'rt_cd':'0','output':{'ODNO':'C'+str(eid)}},observed)
                cycle(observed)
                assert c.execute("SELECT count(*) FROM flow_v3_live_order o JOIN flow_v3_live_intent i USING(intent_id) WHERE i.event_id=%s AND i.side='SELL'",(eid,)).fetchone()[0]==0;c.commit()
            repo=LiveRepository(Pool())
            oid=c.execute('SELECT broker_order_id FROM flow_v3_live_order WHERE intent_id=%s',(iid,)).fetchone()[0];c.commit()
            repo.observe(oid,order_number='CASE'+str(eid),order_date=now.date(),stock_code='0193T0',side='BUY',
                requested_quantity=5,filled_quantity=final,filled_amount=Decimal(final*100),
                status='FILLED' if final==5 else 'CANCELLED',observed_at=observed+timedelta(seconds=1),remaining_quantity=0)
            cycle(observed+timedelta(seconds=2));cycle(observed+timedelta(seconds=2))
            sell=c.execute("SELECT intent_id,quantity FROM flow_v3_live_intent WHERE event_id=%s AND side='SELL'",(eid,)).fetchone();c.commit()
            if final:
                assert sell[1]==final,(initial,final,sell)
                fill(sell[0],'EXIT'+str(eid),final,final*101)
                assert c.execute('SELECT bought_quantity-sold_quantity FROM flow_v3_live_lot WHERE entry_intent_id=%s',(iid,)).fetchone()[0]==0;c.commit()
            else:
                assert sell is None
                assert c.execute('SELECT count(*) FROM flow_v3_live_lot WHERE entry_intent_id=%s',(iid,)).fetchone()[0]==0;c.commit()
        print('PARTIAL_ENTRY_PASS: 5/3->3; cancel-race 5/3->4; full5->5; zero0->no-sell; restart; duplicate0; lot remainder0')
        # Approval tests use TEMP profile/ledgers and a fake HTTP endpoint only.
        from types import SimpleNamespace
        from src.flow_v3.live_transport import FlowTransport
        from src.flow_v3.send_authorization import send_authorized
        for env,db,expected in [('N','N',False),('Y','N',False),('N','Y',False),('Y','Y',True)]:
            c.execute('UPDATE flow_v3_send_profile SET enabled=%s,updated_at=clock_timestamp()', (db,));c.commit()
            with patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':env}):
                assert send_authorized(c)==expected
            c.commit()
        with patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':'Y'}):
            event(500,LONG_IDS[0],datetime(2026,9,9,14))
            event(501,LONG_IDS[0],datetime(2026,9,9,14))
            event(502,LONG_IDS[1],datetime(2026,9,9,14))
            cycle(datetime(2026,9,9,14,1))
            assert c.execute("SELECT count(*) FROM flow_v3_live_order o JOIN flow_v3_live_intent i USING(intent_id) WHERE o.send_enabled AND i.event_id>=500").fetchone()[0]==3;c.commit()
            assert c.execute("SELECT count(*) FROM flow_v3_live_order o JOIN flow_v3_live_intent i USING(intent_id) WHERE o.send_enabled AND i.event_id<500").fetchone()[0]==0;c.commit()
            class ClockConnection:
                def transaction(self): return c.transaction()
                def execute(self,sql,args=None):
                    return c.execute(sql.replace('localtimestamp',"timestamp '2026-09-09 14:01:00'")
                        .replace('current_date',"date '2026-09-09'").replace('localtime',"time '14:01:00'"),args)
            class ClockPool:
                @contextmanager
                def connection(self):
                    try:
                        yield ClockConnection();c.commit()
                    except Exception:
                        c.rollback();raise
            calls=[]
            def fake_post(**kw):
                calls.append(kw)
                return {'rt_cd':'1','msg_cd':'FIXTURE_REJECT','msg1':'TEMP ONLY'}
            transport=FlowTransport(LiveRepository(ClockPool()),SimpleNamespace(post_once=fake_post),
                SimpleNamespace(cano='fixture',account_product_code='fixture'))
            assert transport.run()==3
            assert transport.run()==0
            cycle(datetime(2026,9,9,14,1))
            assert transport.run()==0
            assert len(calls)==3
            assert c.execute('SELECT sum(post_attempt_count) FROM flow_v3_live_order').fetchone()[0]==3;c.commit()
            try:
                with c.transaction():
                    c.execute('UPDATE flow_v3_live_order SET post_attempt_count=2')
                raise AssertionError('MISSING_AT_MOST_ONCE_CONSTRAINT')
            except psycopg.errors.CheckViolation:
                pass
        print('FLOW_SEND_TEMP_PASS: NN/YN/NY blocked; YY 3 fake calls; old orders remain OFF; same-strategy independent entries; duplicate0; count2 constraint rejects; actual HTTP0')
        print('FLOW_LIVE_TEMP_PASS: whitelist15, independent entries/lots, lot exits, duplicate0, broker actual-price separation, costs, variable quantity2, restart, EOD1519, HOLD cutoff, POST0')
        c.rollback()
    finally:
        c.close()


if __name__=='__main__':
    with patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':'N'}):
        main()
