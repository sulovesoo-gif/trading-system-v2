"""Epoch integration tests: exclusively session-local TEMP tables; no orders."""
import os
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from src.first_rise_breakout.j_epoch import DailyCapitalContext, JCapitalEpochRepository, daily_config
from src.first_rise_breakout.config import FirstRiseRuntimeConfig

CONFIG=FirstRiseRuntimeConfig.from_row(('Y','09:01','15:00','09:01','15:00',
                                      '10000000','10000000','100000000'))

DAY=datetime(2026,10,2,8)


class DailyEpochCacheTests(unittest.TestCase):
    def test_epoch_planner_cannot_mix_old_pnl_or_new_config(self):
        from src.first_rise_breakout.j_epoch import EpochState
        from src.first_rise_breakout.j_execution import plan_epoch_entry
        new=EpochState(uuid4(),2,Decimal(20000000),Decimal(10000000),Decimal(100000000),
                       Decimal(0),Decimal(20000000),Decimal(20000000),0)
        args=dict(signal_time=DAY.replace(hour=9,minute=30),signal_sequence=1,
            prior_exit_reason=None,prior_exit_time=None,effective_from=DAY,
            now=DAY.replace(hour=9,minute=31),broker_cash=50000000,price=10000,
            buy_fee_rate=0,same_stock_pending_or_open=False)
        config=replace(CONFIG,start_slot_amount=20000000)
        result=plan_epoch_entry(epoch=new,config=config,**args)
        self.assertEqual(result.quantity,2000)
        self.assertEqual(result.capital_epoch_id,new.epoch_id)
        with self.assertRaisesRegex(ValueError,'EPOCH_CONFIG_MISMATCH'):
            plan_epoch_entry(epoch=new,config=CONFIG,**args)

    def test_daily_cache_and_invalid_next_day(self):
        class Repo:
            calls=0
            fail=False
            config=CONFIG
            def begin_day(self, **kwargs):
                self.calls+=1
                if self.fail: raise ValueError('invalid config')
                return self.config,SimpleNamespace(epoch_id='epoch1')
        repo=Repo();context=DailyCapitalContext(repo)
        context.load(at=DAY)
        repo.config=replace(CONFIG,start_slot_amount=20000000)
        context.load(at=DAY+timedelta(hours=4))
        self.assertEqual(context.config.start_slot_amount,10000000)
        self.assertEqual(repo.calls,1)
        repo.fail=True
        with self.assertLogs('src.first_rise_breakout.j_epoch',level='ERROR'):
            context.load(at=DAY+timedelta(days=1))
        self.assertIsNone(context.config)
        self.assertIsNone(context.epoch_id)
        context.load(at=DAY+timedelta(days=1,hours=2))
        self.assertEqual(repo.calls,2)
        repo.fail=False
        context.load(at=DAY+timedelta(days=2))
        self.assertEqual(context.config.start_slot_amount,20000000)


@unittest.skipUnless(os.getenv('FIRST_RISE_TEMP_PG_ENV'),'requires TEMP PostgreSQL connection')
class PostgresEpochTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from dotenv import load_dotenv
        from src.repository.database import DatabaseSettings
        load_dotenv(os.environ['FIRST_RISE_TEMP_PG_ENV'])
        self.config_reads=0
        testcase=self
        class CountingCursor(psycopg.Cursor):
            def execute(self,query,params=None,**kwargs):
                if str(query).lstrip().upper().startswith('SELECT') and 'FROM common_code' in str(query):
                    testcase.config_reads+=1
                return super().execute(query,params,**kwargs)
        self.c=psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs(),cursor_factory=CountingCursor)
        self.c.execute('SET search_path TO pg_temp')
        self.c.execute("SET statement_timeout='10s'")
        self.c.execute('''CREATE TEMP TABLE common_code(group_cd text,code text,use_yn text,
            attr1 text,attr2 text,attr3 text,attr4 text,attr5 text,attr6 text,attr7 text)''')
        self.c.execute("""INSERT INTO common_code VALUES('FIRST_RISE_RUNTIME','DEFAULT','Y',
            '09:01','15:00','09:01','15:00','10000000','10000000','100000000')""")
        sql=(Path(__file__).resolve().parents[1]/'database/migrations/20261002_first_rise_j_capital_epoch.sql').read_text(encoding='utf-8')
        sql=sql.replace('BEGIN;','').replace('COMMIT;','').replace('CREATE TABLE ','CREATE TEMP TABLE ')
        self.c.execute(sql)
        self.c.commit()
        connection=self.c
        class Pool:
            @contextmanager
            def connection(self): yield connection
        self.pool=Pool();self.repo=JCapitalEpochRepository(self.pool)

    def tearDown(self):
        self.c.close()

    def load(self,day=0):
        at=DAY+timedelta(days=day)
        return self.repo.begin_day(business_date=at.date(),loaded_at=at)

    def bind(self,epoch,day=0):
        trade=uuid4();at=DAY+timedelta(days=day,hours=1)
        with self.c.transaction(),self.c.cursor() as q:
            self.repo.bind_entry(q,trade_id=trade,expected_epoch_id=epoch.epoch_id,
                business_date=at.date(),stock_code='123456',sizing_evidence={'quantity':100,'slot':str(epoch.common_slot_amount)},at=at)
        return trade

    def settle(self,trade,amount,key=None):
        return self.repo.settle_actual(event_key=key or str(uuid4()),trade_id=trade,
            net_pnl_delta=Decimal(amount),settled_at=DAY+timedelta(days=4),
            evidence={'source':'ACTUAL_SETTLEMENT_TEST_FIXTURE'})

    def query(self,sql,args=()):
        with self.c.transaction(): return self.c.execute(sql,args).fetchall()

    def change(self,**attrs):
        with self.c.transaction():
            for column,value in attrs.items():
                assert column in ('attr5','attr6','attr7','use_yn')
                self.c.execute('UPDATE common_code SET '+column+'=%s',(str(value),))

    def test_manual_reset_old_pending_settlement_and_restart(self):
        _,old=self.load();trade=self.bind(old)
        self.settle(trade,'7000000','pnl1')
        self.change(attr5='20000000')
        config,same=self.load()
        self.assertEqual(config.start_slot_amount,10000000)
        self.assertEqual(same.epoch_id,old.epoch_id)
        _,new=self.load(1)
        self.assertNotEqual(old.epoch_id,new.epoch_id)
        self.assertEqual(new.realized_net_pnl,0)
        self.assertEqual(new.compound_reference,20000000)
        self.settle(trade,'3000000','late_old_exit')
        restarted=JCapitalEpochRepository(self.pool)
        _,restored=restarted.begin_day(business_date=(DAY+timedelta(days=1)).date(),loaded_at=DAY+timedelta(days=1,hours=1))
        self.assertEqual(restored.epoch_id,new.epoch_id)
        self.assertEqual(restored.realized_net_pnl,0)
        rows=self.query('SELECT epoch_no,realized_net_pnl FROM first_rise_j_capital_epoch ORDER BY epoch_no')
        self.assertEqual(rows,[(1,Decimal(10000000)),(2,Decimal(0))])
        self.assertEqual(self.query('SELECT epoch_id FROM first_rise_j_capital_binding WHERE trade_id=%s',(trade,))[0][0],old.epoch_id)

    def test_common_code_read_once_even_after_same_day_process_restart(self):
        self.load()
        self.assertEqual(self.config_reads,1)
        self.change(attr5='20000000')
        self.load()
        restarted=JCapitalEpochRepository(self.pool)
        config,_=restarted.begin_day(business_date=DAY.date(),loaded_at=DAY+timedelta(hours=2))
        self.assertEqual(config.start_slot_amount,10000000)
        self.assertEqual(self.config_reads,1)
        self.load(1)
        self.assertEqual(self.config_reads,2)

    def test_entry_reads_latest_pnl_not_daily_cached_pnl(self):
        _,epoch=self.load();trade=self.bind(epoch)
        self.settle(trade,'15000000')
        with self.c.transaction(),self.c.cursor() as q:
            current=self.repo.entry_state(q,epoch_id=epoch.epoch_id,business_date=DAY.date())
        self.assertEqual(current.common_slot_amount,20000000)
        self.assertEqual(current.epoch_id,epoch.epoch_id)

    def test_auto_compounding_and_decrease_no_epoch_reset(self):
        _,epoch=self.load();trade=self.bind(epoch)
        self.settle(trade,'20000000')
        _,up=self.load(1)
        self.assertEqual(up.epoch_id,epoch.epoch_id)
        self.assertEqual(up.common_slot_amount,30000000)
        self.settle(trade,'-15000000')
        _,down=self.load(1)
        self.assertEqual(down.common_slot_amount,10000000)
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_capital_epoch')[0][0],1)

    def test_step_max_changes_rule_only_preserve_entry_evidence(self):
        _,epoch=self.load();trade=self.bind(epoch)
        self.settle(trade,'45000000')
        before=self.query('SELECT entry_sizing_evidence FROM first_rise_j_capital_binding WHERE trade_id=%s',(trade,))
        self.change(attr6='20000000',attr7='30000000')
        _,unchanged=self.load()
        self.assertEqual(unchanged.slot_step_amount,10000000)
        _,updated=self.load(1)
        self.assertEqual(updated.epoch_id,epoch.epoch_id)
        self.assertEqual(updated.realized_net_pnl,45000000)
        self.assertEqual(updated.common_slot_amount,30000000)
        self.assertEqual(self.query('SELECT entry_sizing_evidence FROM first_rise_j_capital_binding WHERE trade_id=%s',(trade,)),before)
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_epoch_rule_day')[0][0],2)

    def test_duplicate_settlement_and_mismatched_retry(self):
        _,epoch=self.load();trade=self.bind(epoch)
        self.assertTrue(self.settle(trade,'7000000','same'))
        self.assertFalse(self.settle(trade,'7000000','same'))
        with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):
            self.settle(trade,'1','same')
        self.assertEqual(self.query('SELECT realized_net_pnl FROM first_rise_j_capital_epoch')[0][0],7000000)

    def test_invalid_config_persisted_same_day_open_settlement_still_works(self):
        _,epoch=self.load();trade=self.bind(epoch)
        self.change(attr5='bad')
        with self.assertRaises(ValueError):self.load(1)
        self.change(attr5='20000000')
        with self.assertRaises(ValueError):self.load(1)
        self.assertTrue(self.settle(trade,'100'))
        _,new=self.load(2)
        self.assertEqual(new.realized_net_pnl,0)

    def test_no_reassignment_or_new_entry_to_retired_epoch(self):
        _,old=self.load();trade=self.bind(old)
        self.change(attr5='20000000');_,new=self.load(1)
        with self.assertRaises(ValueError),self.c.transaction(),self.c.cursor() as q:
            self.repo.bind_entry(q,trade_id=trade,expected_epoch_id=new.epoch_id,
                business_date=(DAY+timedelta(days=1)).date(),stock_code='123456',sizing_evidence={},at=DAY+timedelta(days=1))
        with self.assertRaises(ValueError),self.c.transaction(),self.c.cursor() as q:
            self.repo.bind_entry(q,trade_id=uuid4(),expected_epoch_id=old.epoch_id,
                business_date=DAY.date(),stock_code='123456',sizing_evidence={},at=DAY)

    def test_same_start_no_rollover_and_return_to_old_start_is_new_epoch(self):
        _,a=self.load();_,b=self.load(1)
        self.assertEqual(a.epoch_id,b.epoch_id)
        self.change(attr5='20000000');_,c=self.load(2)
        self.change(attr5='10000000');_,d=self.load(3)
        self.assertEqual(d.epoch_no,3)
        self.assertNotEqual(d.epoch_id,a.epoch_id)


if __name__=='__main__':unittest.main()
