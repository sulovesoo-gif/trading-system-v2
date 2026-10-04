import os
import unittest
from contextlib import contextmanager
from datetime import datetime,time,timedelta
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from src.first_rise_breakout.minute_source import SameDayMinutePeakSource
from src.first_rise_breakout.minute_raw_repository import FirstRiseMinuteRawRepository
from src.first_rise_breakout.j_raw_tracking import market_needs_minutes,completed_market_bars
from src.first_rise_breakout.j_signal import JState
from src.first_rise_breakout.models import CandidateState,ResearchState

DAY=datetime(2026,10,2,9)

def row(minute):
    return dict(bar_time=DAY+timedelta(minutes=minute),stock_code='123456',open_price=D(1000),
        high_price=D(1010),low_price=D(990),close_price=D(1000),volume=1,accumulated_amount=D(1000),
        previous_close_price=D(980),raw_payload={'stck_cntg_hour':(DAY+timedelta(minutes=minute)).strftime('%H%M%S')})

class MemoryRaw:
    def __init__(self):self.rows={};self.fail=False
    def load(self,*,stock_code,as_of):
        return [r for r in self.rows.values() if r['bar_time']<as_of.replace(second=0,microsecond=0)]
    def preserve(self,*,stock_code,as_of,rows,mode):
        if self.fail:raise RuntimeError('storage unavailable')
        for r in rows:self.rows.setdefault(r['bar_time'],r)
        return [self.rows[r['bar_time']] for r in rows]

class SourceRawTests(unittest.TestCase):
    def test_bootstrap_incremental_restart_discard_and_current_bar(self):
        class Collector:
            calls=0
            def collect(self,**kw):
                self.calls+=1
                cursor=datetime.strptime(kw['input_hour'],'%H%M%S')
                m=(cursor.hour-9)*60+cursor.minute
                return [row(i) for i in range(max(0,m-29),m+1)]
        c=Collector();raw=MemoryRaw();source=SameDayMinutePeakSource(c,raw_repository=raw)
        self.assertEqual(len(source.completed_bars_from_open(stock_code='123456',as_of=DAY+timedelta(minutes=10,seconds=30))),10)
        self.assertNotIn(DAY+timedelta(minutes=10),raw.rows)
        source.completed_bars_from_open(stock_code='123456',as_of=DAY+timedelta(minutes=11))
        self.assertEqual(len(raw.rows),11)
        source.discard(stock_code='123456',business_date=DAY.date())
        self.assertEqual(len(raw.rows),11)
        restarted=SameDayMinutePeakSource(c,raw_repository=raw)
        before=c.calls
        restarted.completed_bars_from_open(stock_code='123456',as_of=DAY+timedelta(minutes=12))
        self.assertEqual(c.calls-before,1)
        restarted.completed_bars_from_open(stock_code='123456',as_of=DAY+timedelta(minutes=65))
        self.assertEqual(len(raw.rows),65)
        self.assertEqual(restarted.mode_counts['catch_up'],1)

    def test_failed_persistence_never_returns_unrecorded_bars(self):
        raw=MemoryRaw();raw.fail=True
        source=SameDayMinutePeakSource(SimpleNamespace(collect=lambda **_: [row(0)]),raw_repository=raw)
        with self.assertRaises(RuntimeError):source.completed_bars_from_open(stock_code='123456',as_of=DAY+timedelta(minutes=1))
        self.assertEqual(source._cache,{})

    def test_actual_outcomes_cannot_stop_market_exit_watch(self):
        candidate=CandidateState(uuid4(),DAY.date(),'123456',ResearchState.REJECTED)
        state=JState(candidate,sequence=1,open_signal=candidate)
        for actual_status in ('OVERLAP_SKIP','NO_CAPITAL','REJECT','UNKNOWN','UNFILLED'):
            with self.subTest(actual_status=actual_status):
                self.assertTrue(market_needs_minutes(state,at=DAY.replace(hour=15,minute=31),cutoff=time(15)))
        stopped=JState(candidate,sequence=1,prior_exit_reason='STOP_ENTRY_BREAK')
        self.assertTrue(market_needs_minutes(stopped,at=DAY.replace(hour=14),cutoff=time(15)))
        self.assertFalse(market_needs_minutes(stopped,at=DAY.replace(hour=15),cutoff=time(15)))
        profit=JState(candidate,sequence=1,prior_exit_reason='BOOK_TENKAN_PROFIT')
        self.assertFalse(market_needs_minutes(profit,at=DAY.replace(hour=14),cutoff=time(15)))

@unittest.skipUnless(os.getenv('FIRST_RISE_TEMP_PG_ENV'),'requires TEMP PostgreSQL')
class PgRawTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from dotenv import load_dotenv
        from src.repository.database import DatabaseSettings
        load_dotenv(os.environ['FIRST_RISE_TEMP_PG_ENV'])
        self.c=psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs())
        self.c.execute('SET search_path TO pg_temp')
        sql=(Path(__file__).resolve().parents[1]/'database/migrations/20261002_first_rise_completed_minute_raw.sql').read_text(encoding='utf-8')
        self.c.execute(sql.replace('BEGIN;','').replace('COMMIT;','').replace('CREATE TABLE ','CREATE TEMP TABLE '))
        self.c.commit()
        c=self.c
        class Pool:
            @contextmanager
            def connection(self):yield c
        self.repo=FirstRiseMinuteRawRepository(Pool())
    def tearDown(self):self.c.close()
    def test_duplicate_restart_and_revision_preserve_first(self):
        args=dict(stock_code='123456',as_of=DAY+timedelta(minutes=3),mode='bootstrap')
        self.repo.preserve(rows=[row(0),row(1),row(2),row(3)],**args)
        self.repo.preserve(rows=[row(0),row(1)],**args)
        changed=row(0);changed['close_price']=D(1001)
        with self.assertLogs('src.first_rise_breakout.minute_raw_repository',level='ERROR'):
            canonical=self.repo.preserve(rows=[changed],**args)
        self.assertEqual(canonical[0]['close_price'],1000)
        self.assertEqual(len(self.repo.load(stock_code='123456',as_of=args['as_of'])),3)
    def test_database_rejects_current_bar_even_direct_insert(self):
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation),self.c.transaction():
            self.c.execute('''INSERT INTO first_rise_completed_minute_raw
              (business_date,stock_code,bar_time,open_price,high_price,low_price,close_price,volume,
               fetched_at,observed_as_of,collection_mode,raw_payload)
              VALUES(%s,'123456',%s,1000,1000,1000,1000,1,%s,%s,'bootstrap','{}')''',
              (DAY.date(),DAY,DAY,DAY))
    def test_atomic_failed_batch_and_stock_isolation(self):
        bad=row(1);bad['stock_code']='OTHER'
        with self.assertRaises(ValueError):
            self.repo.preserve(stock_code='123456',as_of=DAY+timedelta(minutes=3),rows=[row(0),bad],mode='bootstrap')
        self.assertEqual(self.repo.load(stock_code='123456',as_of=DAY+timedelta(minutes=3)),[])

    def test_saved_raw_replays_first_and_independent_stop(self):
        from src.first_rise_breakout.config import FirstRiseRuntimeConfig
        from src.first_rise_breakout.raw_replay import replay_market
        config=FirstRiseRuntimeConfig.from_row(('Y','09:01','15:00','09:01','15:00','10000000','10000000','100000000'))
        discovered=DAY+timedelta(minutes=1,seconds=6)
        for minute,high,low in [(0,1030,1030),(1,1025,1020),(9,1031,1025),(10,1032,1020)]:
            item=row(minute)
            item.update(open_price=D(high),high_price=D(high),low_price=D(low),close_price=D(high),previous_close_price=D(1000))
            self.repo.preserve(stock_code='123456',as_of=DAY+timedelta(minutes=minute+1,seconds=6),
                rows=[item],mode='bootstrap' if minute==0 else 'incremental')
        saved=self.repo.load(stock_code='123456',as_of=DAY+timedelta(minutes=12))
        candidate=CandidateState(uuid4(),DAY.date(),'123456',ResearchState.DISCOVERED)
        state,steps=replay_market(candidate=candidate,discovered_at=discovered,rows=saved,config=config)
        self.assertEqual(sum(bool(s.market_entry) for s in steps),1)
        self.assertEqual([s.market_exit.reason for s in steps if s.market_exit],['STOP_ENTRY_BREAK'])
        self.assertEqual(state.prior_exit_time,DAY+timedelta(minutes=10))

if __name__=='__main__':unittest.main()
