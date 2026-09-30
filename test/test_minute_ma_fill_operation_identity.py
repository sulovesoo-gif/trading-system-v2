"""Offline SQL integration: real checkpoint SQL and production identity CHECK.

SQLite adapter removes only PostgreSQL row-lock syntax (concurrency is not
tested here). No production DB, KIS client, or broker transport is instantiated.
"""
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.minute_ma.fill_checkpoint import PostgresMinuteMaFillCheckpointStore


SCHEMA = """
CREATE TABLE minute_ma_live_fill_checkpoint (
 broker_order_id TEXT PRIMARY KEY,broker_order_number TEXT,cumulative_filled_qty INTEGER,
 cumulative_filled_amount NUMERIC,last_avg_fill_price NUMERIC,last_broker_event_time TIMESTAMP,
 version INTEGER,checkpoint_status TEXT,updated_at TEXT);
CREATE TABLE minute_ma_live_intent (
 intent_id TEXT PRIMARY KEY,intent_type TEXT,minute_live_trade_id INTEGER,minute_path_id INTEGER,
 capital_at_signal NUMERIC,minute_policy_path_id INTEGER,minute_policy_operation_id INTEGER,
 underlying_entry_reference_price NUMERIC,stop_threshold_price NUMERIC,stop_policy TEXT,
 real_variant_id INTEGER,real_live_route_id INTEGER,real_capital_epoch_no INTEGER,
 lifecycle_status TEXT,updated_at TEXT);
CREATE TABLE minute_ma_live_order_link (intent_id TEXT,broker_order_id TEXT);
CREATE TABLE minute_ma_operation (operation_id INTEGER,minute_path_id INTEGER,
 capital_epoch_no INTEGER,effective_to TEXT);
CREATE TABLE minute_ma_policy_operation (minute_policy_operation_id INTEGER,capital_epoch_no INTEGER);
CREATE TABLE minute_ma_live_trade (
 minute_live_trade_id INTEGER PRIMARY KEY,minute_path_id INTEGER,operation_id INTEGER,
 capital_epoch_no INTEGER,ownership_id TEXT UNIQUE,trade_status TEXT,capital_at_signal NUMERIC,
 minute_policy_path_id INTEGER,underlying_entry_reference_price NUMERIC,stop_threshold_price NUMERIC,
 stop_policy TEXT,minute_policy_operation_id INTEGER,real_variant_id INTEGER,
 real_live_route_id INTEGER,real_capital_epoch_no INTEGER,entry_filled_amount NUMERIC DEFAULT 0,
 exit_filled_amount NUMERIC DEFAULT 0,updated_at TEXT,
 CONSTRAINT ck_minute_ma_live_trade_operation_identity CHECK (
 ((real_live_route_id IS NOT NULL) AND (real_variant_id IS NOT NULL)
 AND (real_capital_epoch_no IS NOT NULL) AND (operation_id IS NULL)
 AND (minute_policy_operation_id IS NULL)) OR
 ((real_live_route_id IS NULL) AND (real_variant_id IS NULL) AND (real_capital_epoch_no IS NULL)
 AND (((minute_policy_path_id IS NULL) AND (operation_id IS NOT NULL)
 AND (minute_policy_operation_id IS NULL)) OR
 ((minute_policy_path_id IS NOT NULL) AND (operation_id IS NULL)
 AND (minute_policy_operation_id IS NOT NULL))))));
CREATE TABLE minute_ma_live_checkpoint_allocation (
 allocation_id TEXT PRIMARY KEY,broker_order_id TEXT,checkpoint_version INTEGER,
 minute_live_trade_id INTEGER,ownership_id TEXT,stock_code TEXT,side TEXT,
 delta_quantity INTEGER,delta_amount NUMERIC,broker_event_time TEXT,
 UNIQUE(broker_order_id,checkpoint_version));
CREATE TABLE execution_logical_position (
 ownership_type TEXT,ownership_id TEXT,stock_code TEXT,quantity INTEGER,average_cost NUMERIC,
 realized_pnl NUMERIC,last_fill_at TEXT,version INTEGER,updated_at TEXT,
 PRIMARY KEY(ownership_type,ownership_id,stock_code));
CREATE TABLE live_broker_order (broker_order_id TEXT PRIMARY KEY,order_request_id TEXT,status TEXT);
CREATE TABLE live_order_request (order_request_id TEXT PRIMARY KEY,status TEXT);
CREATE TABLE minute_ma_live_capital_reservation (intent_id TEXT,consumed_amount NUMERIC,
 reserved_amount NUMERIC,reservation_status TEXT,updated_at TEXT);
CREATE TABLE minute_ma_live_broker_cost_snapshot (broker_cost_snapshot_id TEXT,
 trade_date TEXT,execution_stock_code TEXT,broker_snapshot_at TEXT,finalization_status TEXT,
 UNIQUE(trade_date,execution_stock_code));
CREATE TABLE live_broker_fill (fill_id TEXT PRIMARY KEY,raw_payload TEXT);
"""


class LocalDB:
    def __init__(self):
        self.db=sqlite3.connect(':memory:')
        self.db.create_function('LEAST',2,min)
        self.db.executescript(SCHEMA)
    def __enter__(self): return self
    def __exit__(self,*exc):
        if exc[0]: self.db.rollback()
    def cursor(self): return Cursor(self.db)
    def commit(self): self.db.commit()


class Cursor:
    def __init__(self,db): self.db=db
    def __enter__(self): return self
    def __exit__(self,*exc): pass
    def execute(self,sql,params=()):
        sql=re.sub(r' FOR UPDATE(?: OF i,l)?','',sql).replace('%s','?')
        self.result=self.db.execute(sql,tuple(str(p) if isinstance(p,(Decimal,date)) else p for p in params))
    def fetchone(self): return self.result.fetchone()


def seed(db,path=7922,variant=9391,route=15,kind='real',side='BUY',trade_id=None,suffix=''):
    order=SimpleNamespace(broker_order_id=f'broker{path}{suffix}',broker_order_number='existing',
        intent_id=f'intent{path}{suffix}',stock_code='0193W0',side=side,quantity=1)
    if not suffix:
        db.db.execute('INSERT INTO minute_ma_operation VALUES(?,?,0,NULL)',(path,path))
        db.db.execute('INSERT INTO minute_ma_policy_operation VALUES(100,3)')
    db.db.execute('''INSERT INTO minute_ma_live_intent(intent_id,intent_type,minute_live_trade_id,
      minute_path_id,capital_at_signal,minute_policy_path_id,minute_policy_operation_id,
      real_variant_id,real_live_route_id,real_capital_epoch_no,lifecycle_status)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(order.intent_id,'ENTRY' if side=='BUY' else 'EXIT',trade_id,
      path,0,10 if kind=='policy' else None,100 if kind=='policy' else None,
      variant if kind=='real' else None,route if kind=='real' else None,
      1 if kind=='real' else None,'ACCEPTED'))
    db.db.execute('INSERT INTO minute_ma_live_order_link VALUES(?,?)',(order.intent_id,order.broker_order_id))
    db.db.execute('INSERT INTO live_broker_order VALUES(?,?,?)',(order.broker_order_id,order.intent_id,'ACCEPTED'))
    db.db.execute('INSERT INTO live_order_request VALUES(?,?)',(order.intent_id,'ACCEPTED'))
    db.db.execute('INSERT INTO live_broker_fill VALUES(?,?)',(order.broker_order_id,'immutable fixture'))
    db.commit()
    return order


def apply(db,order,qty=1):
    return PostgresMinuteMaFillCheckpointStore(lambda:db).apply(order=order,
        cumulative_quantity=qty,cumulative_amount=Decimal(10000)*qty,
        average_price=Decimal(10000),event_time=datetime(2026,9,30,15,18,21))


@pytest.mark.parametrize('path,variant,route',[(7922,9391,15),(8882,9511,21)])
def test_real_accepted_fill_preserves_epoch_and_duplicate_is_noop(path,variant,route):
    db=LocalDB(); order=seed(db,path,variant,route)
    before=db.db.execute('SELECT * FROM live_broker_fill').fetchall()
    assert apply(db,order).status=='ADVANCED'
    assert db.db.execute('''SELECT operation_id,minute_policy_operation_id,real_variant_id,
      real_live_route_id,real_capital_epoch_no,capital_epoch_no,trade_status
      FROM minute_ma_live_trade''').fetchone()==(None,None,variant,route,1,1,'OPEN')
    image='\n'.join(db.db.iterdump())
    assert apply(db,order).status=='DUPLICATE'
    assert '\n'.join(db.db.iterdump())==image
    assert db.db.execute('SELECT * FROM live_broker_fill').fetchall()==before
    assert db.db.execute('SELECT count(*) FROM live_broker_order').fetchone()==(1,)


def test_later_partial_fill_reuses_same_trade_and_ownership():
    db=LocalDB(); order=seed(db);order.quantity=2
    apply(db,order)
    apply(db,order,2)
    assert db.db.execute('SELECT count(*),sum(entry_filled_amount) FROM minute_ma_live_trade').fetchone()==(1,20000)
    assert db.db.execute('SELECT quantity FROM execution_logical_position').fetchone()==(2,)
    assert db.db.execute('SELECT count(*) FROM minute_ma_live_checkpoint_allocation').fetchone()==(2,)
    assert apply(db,order,2).status=='DUPLICATE'


def test_existing_open_normal_exit_retains_original_epoch_and_ownership():
    db=LocalDB();buy=seed(db);apply(db,buy)
    trade_id,ownership=db.db.execute('SELECT minute_live_trade_id,ownership_id FROM minute_ma_live_trade').fetchone()
    sell=seed(db,side='SELL',trade_id=trade_id,suffix='exit')
    before=db.db.execute('SELECT * FROM live_broker_fill ORDER BY fill_id').fetchall()
    apply(db,sell)
    assert db.db.execute('SELECT trade_status,real_capital_epoch_no,ownership_id FROM minute_ma_live_trade').fetchone()==('CLOSED',1,ownership)
    assert db.db.execute('SELECT quantity FROM execution_logical_position').fetchone()==(0,)
    assert db.db.execute('SELECT count(*) FROM minute_ma_live_broker_cost_snapshot').fetchone()==(1,)
    assert db.db.execute('SELECT * FROM live_broker_fill ORDER BY fill_id').fetchall()==before
    assert apply(db,sell).status=='DUPLICATE'


@pytest.mark.parametrize('kind,expected',[('legacy',(7922,None,0)),('policy',(None,100,3))])
def test_legacy_and_policy_identity_unchanged(kind,expected):
    db=LocalDB();apply(db,seed(db,kind=kind))
    assert db.db.execute('SELECT operation_id,minute_policy_operation_id,capital_epoch_no FROM minute_ma_live_trade').fetchone()==expected


def test_production_check_still_rejects_mixed_identity():
    db=LocalDB()
    with pytest.raises(sqlite3.IntegrityError,match='operation_identity'):
        db.db.execute('''INSERT INTO minute_ma_live_trade(operation_id,real_variant_id,
          real_live_route_id,real_capital_epoch_no) VALUES(7922,9391,15,1)''')


def test_original_join_reproduces_failure_and_rolls_back_checkpoint(monkeypatch):
    original=Cursor.execute
    def old_join(self,sql,params=()):
        return original(self,sql.replace('AND i.real_live_route_id IS NULL',''),params)
    monkeypatch.setattr(Cursor,'execute',old_join)
    db=LocalDB();order=seed(db)
    with pytest.raises(sqlite3.IntegrityError,match='operation_identity'):
        apply(db,order)
    assert db.db.execute('SELECT count(*) FROM minute_ma_live_fill_checkpoint').fetchone()==(0,)
    assert db.db.execute('SELECT status FROM live_broker_order').fetchone()==('ACCEPTED',)
