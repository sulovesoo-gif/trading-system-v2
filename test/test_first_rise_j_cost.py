"""V1.8 costs. PostgreSQL tests use only session TEMP objects and fake orders."""
import os
import unittest
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from uuid import uuid4

from psycopg.types.json import Jsonb
from src.first_rise_breakout.j_cost import provisional_costs, record_checkpoint, close_actual, settle_final_costs
from src.broker.shared_cost_allocation import OwnedCheckpoint, allocate_shared_costs
from src.daily_ma_v03.broker_cost_allocation import BrokerCostSnapshot, BrokerCostTotals, BrokerCostStatus
from test.test_first_rise_j_epoch import PostgresEpochTests, DAY


class CostFormulaTests(unittest.TestCase):
    def test_actual_amount_no_slippage(self):
        self.assertEqual(provisional_costs(10000000,11000000),
                         (D('1465.270000000'),D('1611.797000000'),D(22000),D(0)))

    def test_nonfinite_or_invalid_amount_blocked(self):
        for amount in ('NaN','Infinity','-1','0'):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                provisional_costs(amount,100)

    def test_multi_strategy_and_multiple_positions_reconcile_once(self):
        snapshot=BrokerCostSnapshot(DAY.date(),'123456',BrokerCostTotals(D(101),D(103),D(201)),
            DAY+timedelta(days=3),True,BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK)
        fills=[OwnedCheckpoint(family,trade,f'{family}-{trade}-{side}',1,side,1,D(amount))
               for family,trade,amount in [('MINUTE',1,100),('FIRST_RISE',1,100),('FIRST_RISE',2,200)]
               for side in ('BUY','SELL')]
        _,allocations=allocate_shared_costs(snapshot=snapshot,checkpoints=fills)
        self.assertEqual(len(allocations),6)
        self.assertEqual(tuple(sum(getattr(a,f) for _,a in allocations)
                               for f in ('buy_fee','sell_fee','sell_tax')),(101,103,201))
        self.assertEqual({key[0] for key,_ in allocations},{'MINUTE','FIRST_RISE'})
        own=sum(a.buy_fee for key,a in allocations if key[0]=='FIRST_RISE')
        self.assertLess(own,101)
        self.assertEqual(allocate_shared_costs(snapshot=snapshot,checkpoints=fills),
                         allocate_shared_costs(snapshot=snapshot,checkpoints=list(reversed(fills))))


@unittest.skipUnless(os.getenv('FIRST_RISE_TEMP_PG_ENV'),'requires TEMP PostgreSQL connection')
class PostgresCostTests(PostgresEpochTests):
    def setUp(self):
        super().setUp()
        root=Path(__file__).resolve().parents[1]
        for filename,table in [('30_live_order_planning.sql','live_order_request'),
                               ('31_live_broker_contract.sql','live_broker_order')]:
            text=(root/'database/ddl'/filename).read_text(encoding='utf-8')
            statement=next(s.strip() for s in text.split(';') if s.strip().startswith('CREATE TABLE '+table+'('))
            self.c.execute(statement.replace('CREATE TABLE ','CREATE TEMP TABLE ',1))
        shared=(root/'database/migrations/20260909_broker_shared_cost.sql').read_text(encoding='utf-8')
        shared=shared.split('CREATE TABLE IF NOT EXISTS flow_v3_live_checkpoint_allocation')[0]
        self.c.execute(shared.replace('BEGIN;','').replace('CREATE TABLE IF NOT EXISTS ','CREATE TEMP TABLE '))
        cost=(root/'database/migrations/20261002_first_rise_j_live_cost.sql').read_text(encoding='utf-8')
        self.c.execute(cost.replace('BEGIN;','').replace('COMMIT;','').replace('CREATE TABLE ','CREATE TEMP TABLE '))
        self.c.commit()

    def order(self,trade,side,quantity=100,strategy='FIRST_RISE_J_V1.3',status='FILLED'):
        request,broker=uuid4(),uuid4()
        with self.c.transaction():
            self.c.execute('''INSERT INTO live_order_request
              (order_request_id,idempotency_key,strategy_instance_id,source_intent_id,source_decision_id,
               execution_stock_code,side,requested_notional,requested_quantity,reference_price,order_type,
               execution_target_time,strategy_capital_before,reserved_capital,safety_status,status,reason,detail)
              VALUES(%s,%s,%s,%s,%s,'123456',%s,10000000,%s,100000,'MARKET',%s,10000000,0,'TEST',%s,'TEST',%s)''',
              (request,str(request),strategy,uuid4(),uuid4(),side,quantity,DAY,status,Jsonb({'first_rise_trade_id':str(trade)})))
            self.c.execute('''INSERT INTO live_broker_order
              (broker_order_id,order_request_id,strategy_instance_id,execution_stock_code,side,quantity,
               client_order_key,status,payload) VALUES(%s,%s,%s,'123456',%s,%s,%s,%s,'{}')''',
              (broker,request,strategy,side,quantity,str(broker),status))
        return broker

    def fill(self,trade,broker,amount,quantity=100,version=1,at=DAY):
        with self.c.transaction(),self.c.cursor() as q:
            return record_checkpoint(q,trade_id=trade,broker_order_id=broker,version=version,
                quantity=quantity,amount=D(amount),at=at)

    def completed(self,sell=21000000):
        _,epoch=self.load();trade=self.bind(epoch)
        buy=self.order(trade,'BUY');exit_order=self.order(trade,'SELL')
        self.fill(trade,buy,10000000);self.fill(trade,exit_order,sell)
        return epoch,trade

    def close(self,trade):
        with self.c.transaction(),self.c.cursor() as q:
            return close_actual(q,trade_id=trade,at=DAY+timedelta(hours=2))

    def finalized(self,trade,actual_cost=31420):
        cost_id=self.query('SELECT cost_trade_id FROM first_rise_j_live_cost WHERE trade_id=%s',(trade,))[0][0]
        with self.c.transaction():
            self.c.execute('''INSERT INTO broker_shared_cost_snapshot
                (trade_date,execution_stock_code,buy_fee,sell_fee,sell_tax,other_cost,broker_snapshot_at,
                 status,fingerprint,confirmation_count) VALUES(%s,'123456',1000,1000,%s,0,%s,
                 'FINALIZED_BY_STABLE_RECHECK','fixture',2) ON CONFLICT DO NOTHING''',
                (DAY.date(),actual_cost-2000,DAY+timedelta(days=3)))
            self.c.execute('''INSERT INTO broker_shared_cost_allocation
                (trade_date,execution_stock_code,family,live_trade_id,side,fill_notional,buy_fee,sell_fee,sell_tax,other_cost)
                SELECT broker_event_time::date,stock_code,'FIRST_RISE',cost_trade_id,side,sum(delta_amount),
                  CASE WHEN side='BUY' THEN 1000 ELSE 0 END,CASE WHEN side='SELL' THEN 1000 ELSE 0 END,
                  CASE WHEN side='SELL' THEN %s ELSE 0 END,0
                FROM first_rise_j_live_checkpoint_allocation WHERE cost_trade_id=%s GROUP BY 1,2,4,5
                ON CONFLICT DO NOTHING''',(actual_cost-2000,cost_id))
        return cost_id

    def final(self,cost_id):
        with self.c.transaction(),self.c.cursor() as q:
            return settle_final_costs(q,cost_trade_id=cost_id,at=DAY+timedelta(days=3))

    def test_close_immediate_tier_and_other_open_unchanged(self):
        epoch,trade=self.completed()
        other=self.bind(epoch)
        before=self.query('SELECT entry_sizing_evidence FROM first_rise_j_capital_binding WHERE trade_id=%s',(other,))
        self.assertTrue(self.close(trade));self.assertFalse(self.close(trade))
        net=D(11000000)-sum(provisional_costs(10000000,21000000))
        self.assertEqual(self.query('SELECT realized_net_pnl,common_slot_amount FROM first_rise_j_capital_epoch'),[(net,D(20000000))])
        self.assertEqual(self.query('SELECT entry_sizing_evidence FROM first_rise_j_capital_binding WHERE trade_id=%s',(other,)),before)
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_realized_event')[0][0],1)
        from src.first_rise_breakout.j_capital import size_buy
        config,current=self.load()
        sizing=size_buy(config=config,realized_net_pnl=current.realized_net_pnl,
                        broker_cash=50000000,price=10000,buy_fee_rate=D('0.000146527'))
        self.assertEqual(sizing.common_slot_amount,20000000)
        self.assertEqual(sizing.quantity,1999)

    def test_positive_negative_delta_exact_final_no_double_charge(self):
        for actual in (50000,3000):
            with self.subTest(actual=actual):
                _,trade=self.completed(11000000);self.close(trade)
                cost_id=self.finalized(trade,actual)
                self.assertTrue(self.final(cost_id));self.assertFalse(self.final(cost_id))
                provisional,delta,final=self.query('''SELECT provisional_net_realized_pnl,settlement_delta,
                    final_net_realized_pnl FROM first_rise_j_live_cost WHERE trade_id=%s''',(trade,))[0]
                self.assertEqual(provisional+delta,final)
                self.assertEqual(final,D(1000000)-actual)
                self.assertEqual(delta>0,actual==3000)

    def test_old_epoch_actual_delta_does_not_touch_new(self):
        old,trade=self.completed(11000000);self.close(trade)
        self.change(attr5=20000000);_,new=self.load(1)
        self.final(self.finalized(trade,31420))
        self.assertEqual(self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch WHERE epoch_id=%s',(new.epoch_id,)),[(D(0),)])
        self.assertEqual(self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch WHERE epoch_id=%s',(old.epoch_id,)),[(D(968580),)])

    def test_multiple_positions_one_epoch(self):
        _,a=self.completed(11000000);_,b=self.completed(12000000)
        self.close(a);self.close(b)
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_capital_epoch')[0][0],1)
        self.assertEqual(self.query('SELECT DISTINCT ownership FROM first_rise_j_live_cost'),[('FIRST_RISE',)])
        self.assertEqual(self.query('SELECT sum(provisional_net_realized_pnl) FROM first_rise_j_live_cost'),
                         self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch'))

    def test_pending_costs_do_not_finalize(self):
        _,trade=self.completed();self.close(trade)
        cost_id=self.query('SELECT cost_trade_id FROM first_rise_j_live_cost WHERE trade_id=%s',(trade,))[0][0]
        self.assertFalse(self.final(cost_id))

    def test_checkpoint_replay_ownership_and_close_barrier(self):
        _,epoch=self.load();trade=self.bind(epoch)
        other=self.order(trade,'BUY',strategy='MINUTE_MA')
        with self.assertRaisesRegex(ValueError,'OWNERSHIP'):self.fill(trade,other,10000000)
        buy=self.order(trade,'BUY');sell=self.order(trade,'SELL',status='PARTIALLY_FILLED')
        self.assertTrue(self.fill(trade,buy,10000000))
        self.assertFalse(self.fill(trade,buy,10000000))
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY'):self.fill(trade,buy,1)
        self.fill(trade,sell,11000000)
        with self.assertRaisesRegex(ValueError,'PENDING_ORDER'):self.close(trade)
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_realized_event')[0][0],0)

    def test_transaction_rollback_cannot_leave_half_applied_compound(self):
        _,trade=self.completed()
        with self.assertRaises(RuntimeError),self.c.transaction(),self.c.cursor() as q:
            close_actual(q,trade_id=trade,at=DAY)
            raise RuntimeError('simulated crash before commit')
        self.assertEqual(self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch'),[(D(0),)])
        self.assertTrue(self.close(trade))

    def test_shared_family_constraint_and_epoch_fk(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ForeignKeyViolation),self.c.transaction():
            self.c.execute('INSERT INTO first_rise_j_live_cost(trade_id,epoch_id) VALUES(%s,%s)',(uuid4(),uuid4()))
        with self.assertRaises(psycopg.errors.CheckViolation),self.c.transaction():
            self.c.execute("""INSERT INTO broker_shared_cost_allocation VALUES
                (%s,'123456','SLOT_A',1,'BUY',1,0,0,0,0,now())""",(DAY.date(),))

    def test_real_shared_finalizer_preserves_other_families_and_settles_first_rise(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        from src.broker.shared_cost_repository import SharedBrokerCostFinalizer
        _,trade=self.completed(11000000);self.close(trade)
        with self.c.transaction():
            self.c.execute('CREATE TEMP TABLE daily_strategy_live_trade(live_trade_id bigint,ownership_id text)')
            self.c.execute('''CREATE TEMP TABLE daily_strategy_live_checkpoint_allocation
                (broker_order_id uuid,checkpoint_version int,ownership_id text,stock_code text,
                 side text,delta_quantity int,delta_amount numeric,broker_event_time timestamp)''')
            self.c.execute('''CREATE TEMP TABLE minute_ma_live_checkpoint_allocation
                (broker_order_id uuid,checkpoint_version int,minute_live_trade_id bigint,stock_code text,
                 side text,delta_quantity int,delta_amount numeric,broker_event_time timestamp)''')
            self.c.execute('''CREATE TEMP TABLE flow_v3_live_checkpoint_allocation
                (broker_order_id uuid,checkpoint_version int,live_trade_id bigint,stock_code text,
                 side text,delta_quantity int,delta_amount numeric,broker_event_time timestamp,broker_trade_date date)''')
            for prefix,id_column in [('daily_strategy_live','live_trade_id'),('minute_ma_live','minute_live_trade_id')]:
                self.c.execute(f'''CREATE TEMP TABLE {prefix}_broker_cost_snapshot(
                    broker_cost_snapshot_id uuid,trade_date date,execution_stock_code text,
                    broker_buy_fee numeric,broker_sell_fee numeric,broker_sell_tax numeric,broker_other_cost numeric,
                    broker_snapshot_at timestamp,finalization_status text,finalized_at timestamp,
                    stable_confirmation_count int,fill_set_fingerprint text,last_stable_recheck_at timestamp,
                    UNIQUE(trade_date,execution_stock_code))''')
                self.c.execute(f'''CREATE TEMP TABLE {prefix}_broker_cost_allocation(
                    broker_cost_snapshot_id uuid,{id_column} bigint,allocation_side text,fill_notional numeric,
                    allocated_buy_fee numeric,allocated_sell_fee numeric,allocated_sell_tax numeric,
                    allocated_other_cost numeric,stable_allocation_key text UNIQUE)''')
            self.c.execute("INSERT INTO daily_strategy_live_trade VALUES(1,'DAILY_OWNER')")
            for side in ('BUY','SELL'):
                self.c.execute("INSERT INTO daily_strategy_live_checkpoint_allocation VALUES(%s,1,'DAILY_OWNER','123456',%s,100,10000000,%s)",(uuid4(),side,DAY))
                self.c.execute("INSERT INTO minute_ma_live_checkpoint_allocation VALUES(%s,1,1,'123456',%s,100,10000000,%s)",(uuid4(),side,DAY))
                self.c.execute("INSERT INTO flow_v3_live_checkpoint_allocation VALUES(%s,1,1,'123456',%s,100,10000000,%s,%s)",(uuid4(),side,DAY,DAY.date()))
        c=self.c
        @contextmanager
        def factory():
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
        class Lookup:
            now=DAY+timedelta(days=3)
            def lookup(self,**kwargs):
                return SimpleNamespace(totals=BrokerCostTotals(D(4000),D(4100),D(82000)),broker_snapshot_at=self.now)
        lookup=Lookup()
        finalizer=SharedBrokerCostFinalizer(connection_factory=factory,cost_lookup=lookup,
            calendar=SimpleNamespace(open_dates=lambda *_:[lookup.now.date()]))
        self.assertEqual(finalizer.finalize_due(today=DAY.date())['finalized'],0)
        self.assertEqual(finalizer.finalize_due(today=lookup.now.date())['finalized'],0)
        lookup.now+=timedelta(minutes=10)
        self.assertEqual(finalizer.finalize_due(today=lookup.now.date())['finalized'],1)
        self.assertEqual(self.query('SELECT sum(buy_fee),sum(sell_fee),sum(sell_tax) FROM broker_shared_cost_allocation'),[(D(4000),D(4100),D(82000))])
        self.assertEqual(self.query('SELECT count(*) FROM broker_shared_cost_allocation')[0][0],8)
        for family,prefix in [('DAILY','daily_strategy_live'),('MINUTE','minute_ma_live')]:
            self.assertEqual(self.query('SELECT sum(buy_fee),sum(sell_fee),sum(sell_tax) FROM broker_shared_cost_allocation WHERE family=%s',(family,)),
                self.query(f'SELECT sum(allocated_buy_fee),sum(allocated_sell_fee),sum(allocated_sell_tax) FROM {prefix}_broker_cost_allocation'))
        self.assertEqual(self.query('SELECT final_net_realized_pnl FROM first_rise_j_live_cost'),
                         self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch'))
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_realized_event')[0][0],2)
        before=self.query('SELECT * FROM broker_shared_cost_allocation ORDER BY family,side')
        self.assertEqual(finalizer.finalize_due(today=lookup.now.date())['finalized'],0)
        self.assertEqual(before,self.query('SELECT * FROM broker_shared_cost_allocation ORDER BY family,side'))


if __name__=='__main__':unittest.main()
