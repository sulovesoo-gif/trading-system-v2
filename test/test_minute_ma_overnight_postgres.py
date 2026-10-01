"""Opt-in tests using only session-local TEMP clones; no broker/network orders."""
import os
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from src.minute_ma.real_live import PostgresMinuteMaRealLivePlanner
from src.minute_ma.real_paper_runtime import MinuteMaRealPaperRuntime
from src.minute_ma.real_paper import account_costs
from src.minute_ma.contracts import MinuteBar
from src.minute_ma.engine import SignalEvent, SignalType
from test.test_minute_ma_overnight_exit import OvernightPolicy, Pool, DAY, at, bars, UP, DOWNFLAT
from test.test_minute_ma_real_official_signals import path, route

pytestmark=pytest.mark.skipif(not os.getenv('MINUTE_MA_TEST_ENV'),reason='requires opt-in TEMP PostgreSQL session')

TABLES=('minute_ma_real_paper_trade','minute_ma_real_capital_epoch','minute_ma_live_trade',
        'minute_ma_live_intent','minute_ma_live_order_link','minute_ma_live_signal_event',
        'live_order_request','execution_logical_position')

@pytest.fixture
def db():
    import psycopg
    from dotenv import load_dotenv
    from src.repository.database import DatabaseSettings
    load_dotenv(os.environ['MINUTE_MA_TEST_ENV'])
    c=psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs())
    c.execute('SET search_path TO pg_temp')
    for table in TABLES:
        c.execute(f'CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING DEFAULTS INCLUDING CONSTRAINTS INCLUDING INDEXES)')
        # No fixture may consume an actual production sequence.
        defaults=c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema LIKE 'pg_temp_%%' AND table_name=%s AND column_default LIKE 'nextval%%'",(table,)).fetchall()
        for (column,) in defaults: c.execute(f'ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT')
    c.commit()
    class TempPool:
        @contextmanager
        def connection(self):
            try: yield c
            except BaseException:
                c.rollback()
                raise
    pool=TempPool()
    yield c,pool
    c.close()

def forced(price=101):
    return OvernightPolicy(Pool()).event(path(),DAY,bars(price),(),at(11))

def paper_seed(c,trade_id,day=30,epoch=1):
    c.execute("""INSERT INTO minute_ma_real_paper_trade(
      real_paper_trade_id,real_variant_id,minute_strategy_id,strategy_id,signal_code,filter_code,paper_epoch,
      lifecycle_status,entry_signal_key,entry_signal_time,entry_execution_time,entry_price,
      compound_quantity,fixed_quantity,entry_realized_capital,real_is_complete)
      VALUES(%s,1,7921,'1981','005930','BASE',%s,'OPEN',%s,%s,%s,100,10,20,10000000,FALSE)""",
      (trade_id,epoch,str(trade_id),datetime(2026,9,day,15),datetime(2026,9,day,15,1)))

@pytest.mark.parametrize('price,reason',[(101,UP),(99,DOWNFLAT)])
def test_paper_independent_trades_costs_epoch_and_restart(db,price,reason):
    c,pool=db
    c.execute("""INSERT INTO minute_ma_real_capital_epoch(real_capital_epoch_id,real_variant_id,paper_epoch,
      initial_capital,current_realized_capital,effective_from,ended_at) VALUES(1,1,1,10000000,10000000,'2026-09-01','2026-09-30')""")
    paper_seed(c,1); paper_seed(c,2)
    c.commit()
    e=forced(price); execution=MinuteBar(e.source_bar_time,110,110,110,110)
    runtime=MinuteMaRealPaperRuntime(pool)
    assert runtime._close_incremental(path(),e,execution)==2
    assert runtime._close_incremental(path(),e,execution)==0
    rows=c.execute('SELECT lifecycle_status,exit_reason,compound_realized_pnl,fixed_realized_pnl,paper_epoch FROM minute_ma_real_paper_trade ORDER BY real_paper_trade_id').fetchall()
    compound=account_costs(10,Decimal(100),Decimal(110)).realized_pnl
    fixed=account_costs(20,Decimal(100),Decimal(110)).realized_pnl
    assert rows==[('CLOSED',reason,compound,fixed,1)]*2
    assert c.execute('SELECT current_realized_capital FROM minute_ma_real_capital_epoch').fetchone()[0]==10000000+compound*2

def live_seed(c,trade_id,status='OPEN',quantity=1):
    c.execute("""INSERT INTO minute_ma_live_trade(minute_live_trade_id,minute_path_id,capital_epoch_no,
      ownership_id,trade_status,capital_at_signal,real_variant_id,real_live_route_id,real_capital_epoch_no)
      VALUES(%s,7921,1,%s,%s,0,1981,1981,1)""",(trade_id,f'test:{trade_id}',status))
    c.execute("""INSERT INTO execution_logical_position(ownership_type,ownership_id,stock_code,quantity)
      VALUES('MINUTE_MA',%s,'0193W0',%s)""",(f'test:{trade_id}',quantity))
    c.execute("""INSERT INTO minute_ma_live_intent(intent_id,intent_key,minute_path_id,minute_live_trade_id,
      intent_type,source_event_time,requested_quantity,capital_at_signal,lifecycle_status)
      VALUES(%s,%s,7921,%s,'ENTRY','2026-09-30 15:00',%s,0,'FILLED')""",
      (uuid4(),str(uuid4()),trade_id,quantity))
    c.commit()

@pytest.mark.parametrize('price,reason',[(101,UP),(99,DOWNFLAT)])
def test_live_multiple_open_no_duplicates_real_reason_and_epoch(db,price,reason):
    c,pool=db
    live_seed(c,1);live_seed(c,2);live_seed(c,3,'CLOSED',0)
    planner=PostgresMinuteMaRealLivePlanner(pool.connection)
    e=forced(price)
    assert planner.plan_exit(route=route(),event=e,reference_price=Decimal(110))=={'READY_FOR_BROKER':2}
    assert planner.plan_exit(route=route(),event=e,reference_price=Decimal(110))=={'READY_FOR_BROKER':2}
    assert c.execute("SELECT count(*) FROM minute_ma_live_intent WHERE intent_type='EXIT'").fetchone()[0]==2
    assert c.execute('SELECT count(*) FROM live_order_request').fetchone()[0]==2
    rows=c.execute("SELECT i.exit_reason,i.real_capital_epoch_no,t.operation_id IS NULL FROM minute_ma_live_intent i LEFT JOIN minute_ma_live_trade t ON t.minute_live_trade_id=i.target_minute_live_trade_id WHERE i.intent_type='EXIT'").fetchall()
    assert rows==[(reason,1,True)]*2

def test_pending_partial_exit_prevents_second_exit(db):
    c,pool=db
    live_seed(c,1,quantity=3)
    planner=PostgresMinuteMaRealLivePlanner(pool.connection)
    first=forced(101)
    planner.plan_exit(route=route(),event=first,reference_price=Decimal(110))
    c.execute("UPDATE minute_ma_live_intent SET lifecycle_status='PARTIALLY_FILLED' WHERE intent_type='EXIT'")
    c.execute("UPDATE execution_logical_position SET quantity=2")
    c.commit()
    result=planner.plan_exit(route=route(),event=forced(99),reference_price=Decimal(110))
    assert result=={'EXIT_ALREADY_PENDING':1}
    assert c.execute('SELECT count(*) FROM live_order_request').fetchone()[0]==1

def normal():
    return SignalEvent(7921,path().path_key,SignalType.EXIT,at(2),at(3),'normal-ma-exit',True,{}, {})

def test_paper_normal_first_and_current_day_open_untouched(db):
    c,pool=db
    c.execute("""INSERT INTO minute_ma_real_capital_epoch(real_capital_epoch_id,real_variant_id,paper_epoch,
      initial_capital,current_realized_capital,effective_from) VALUES(1,1,1,10000000,10000000,'2026-09-01')""")
    paper_seed(c,1);paper_seed(c,2)
    c.execute("UPDATE minute_ma_real_paper_trade SET entry_signal_time='2026-10-01 14:50',entry_execution_time='2026-10-01 14:51' WHERE real_paper_trade_id=2")
    c.commit()
    runtime=MinuteMaRealPaperRuntime(pool)
    assert runtime._close_incremental(path(),normal(),MinuteBar(at(3),110,110,110,110))==1
    assert runtime._close_incremental(path(),forced(),MinuteBar(at(3),110,110,110,110))==0
    assert c.execute('SELECT lifecycle_status,exit_reason FROM minute_ma_real_paper_trade ORDER BY real_paper_trade_id').fetchall()==[('CLOSED','NORMAL_EXIT'),('OPEN',None)]

def test_live_normal_first_and_rejected_exit_not_replayed(db):
    c,pool=db
    live_seed(c,1)
    planner=PostgresMinuteMaRealLivePlanner(pool.connection)
    assert planner.plan_exit(route=route(),event=normal(),reference_price=Decimal(110))=={'READY_FOR_BROKER':1}
    assert planner.plan_exit(route=route(),event=forced(),reference_price=Decimal(110))=={'EXIT_ALREADY_PENDING':1}
    c.execute("UPDATE minute_ma_live_intent SET lifecycle_status='REJECTED' WHERE intent_type='EXIT'")
    c.commit()
    assert planner.plan_exit(route=route(),event=normal(),reference_price=Decimal(110))=={'REJECTED':1}
    assert c.execute('SELECT count(*) FROM live_order_request').fetchone()[0]==1
    assert c.execute('SELECT quantity FROM execution_logical_position').fetchone()[0]==1
    assert c.execute("SELECT real_capital_epoch_no,trade_status FROM minute_ma_live_trade").fetchone()==(1,'OPEN')
