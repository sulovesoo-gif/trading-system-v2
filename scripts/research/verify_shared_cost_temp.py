"""Production cost repository against session-local fixtures; no broker calls."""
import sys
from contextlib import contextmanager
from datetime import date,datetime,timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT=Path('/home/ubuntu/projects/trading-system-v2')
sys.path.insert(0,str(ROOT))
import psycopg
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
from src.broker.shared_cost_repository import SharedBrokerCostFinalizer
from src.daily_ma_v03.broker_cost_allocation import BrokerCostTotals


def main(migration):
    load_dotenv(ROOT/'.env')
    c=psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs(),autocommit=True)
    try:
        c.execute('CREATE TEMP TABLE flow_v3_live_trade(live_trade_id bigint PRIMARY KEY)')
        c.execute('SET search_path TO pg_temp,public')
        c.execute('CREATE TEMP TABLE daily_strategy_live_trade(live_trade_id bigint PRIMARY KEY,ownership_id text)')
        for prefix in ('daily_strategy_live','minute_ma_live'):
            for suffix in ('broker_cost_snapshot','broker_cost_allocation'):
                c.execute(f'CREATE TEMP TABLE {prefix}_{suffix} (LIKE public.{prefix}_{suffix} INCLUDING ALL)')
        c.execute('''CREATE TEMP TABLE daily_strategy_live_checkpoint_allocation
          (broker_order_id uuid,checkpoint_version int,ownership_id text,stock_code text,side text,
           delta_quantity int,delta_amount numeric,broker_event_time timestamp)''')
        c.execute('''CREATE TEMP TABLE minute_ma_live_checkpoint_allocation
          (broker_order_id uuid,checkpoint_version int,minute_live_trade_id bigint,stock_code text,side text,
           delta_quantity int,delta_amount numeric,broker_event_time timestamp)''')
        c.execute(migration.replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE IF NOT EXISTS'))
        c.execute('ALTER TABLE flow_v3_live_checkpoint_allocation ADD COLUMN IF NOT EXISTS broker_trade_date date')
        c.execute("INSERT INTO daily_strategy_live_trade VALUES(1,'D1')")
        c.execute('INSERT INTO flow_v3_live_trade VALUES(1)')
        for side in ('BUY','SELL'):
            c.execute("INSERT INTO daily_strategy_live_checkpoint_allocation VALUES(%s,1,'D1','0193T0',%s,1,100,'2026-09-08 10:00')",(uuid4(),side))
            c.execute("INSERT INTO minute_ma_live_checkpoint_allocation VALUES(%s,1,1,'0193T0',%s,1,200,'2026-09-08 10:00')",(uuid4(),side))
            c.execute("INSERT INTO flow_v3_live_checkpoint_allocation(broker_order_id,checkpoint_version,live_trade_id,stock_code,side,delta_quantity,delta_amount,broker_event_time) VALUES(%s,1,1,'0193T0',%s,1,300,'2026-09-08 10:00')",(uuid4(),side))
        c.autocommit=False
        @contextmanager
        def factory():
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
        class Lookup:
            now=datetime(2026,9,9,10)
            def lookup(self,**_):
                return SimpleNamespace(totals=BrokerCostTotals(Decimal(101),Decimal(53),Decimal(17)),broker_snapshot_at=self.now)
        lookup=Lookup()
        repo=SharedBrokerCostFinalizer(connection_factory=factory,cost_lookup=lookup,
            calendar=SimpleNamespace(open_dates=lambda *_:[date(2026,9,9)]))
        assert repo.finalize_due(today=date(2026,9,9))['finalized']==0
        lookup.now+=timedelta(minutes=1)
        assert repo.finalize_due(today=date(2026,9,9))['finalized']==0
        lookup.now+=timedelta(minutes=9)
        assert repo.finalize_due(today=date(2026,9,9))['finalized']==1
        before=c.execute('SELECT * FROM broker_shared_cost_allocation ORDER BY family,side').fetchall()
        c.commit()
        assert len(before)==6
        assert repo.finalize_due(today=date(2026,9,9))['finalized']==0
        assert before==c.execute('SELECT * FROM broker_shared_cost_allocation ORDER BY family,side').fetchall()
        totals=c.execute('SELECT sum(buy_fee),sum(sell_fee),sum(sell_tax) FROM broker_shared_cost_allocation').fetchone()
        assert totals==(101,53,17)
        for family,prefix in [('DAILY','daily_strategy_live'),('MINUTE','minute_ma_live')]:
            own=c.execute('SELECT sum(buy_fee),sum(sell_fee),sum(sell_tax) FROM broker_shared_cost_allocation WHERE family=%s',(family,)).fetchone()
            published=c.execute(f'SELECT sum(allocated_buy_fee),sum(allocated_sell_fee),sum(allocated_sell_tax) FROM {prefix}_broker_cost_allocation').fetchone()
            assert own==published
        print('SHARED_COST_TEMP_PASS: 3 families / 6 allocations / totals 101,53,17 / replay identical / consumer slices identical / broker calls 0',flush=True)
        c.rollback()
    finally:
        c.close()


if __name__=='__main__':
    main((ROOT/'database/migrations/20260909_broker_shared_cost.sql').read_text())
