import os
import unittest
from pathlib import Path
from types import SimpleNamespace

from test import test_first_rise_minute_raw as raw_fixture
from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from src.first_rise_breakout.config import FirstRiseRuntimeConfig
from src.first_rise_breakout.models import CandidateState, ResearchState, MinuteBar
from src.first_rise_breakout.j_repository import JMarketRepository
from src.first_rise_breakout.j_runtime import JMarketRuntime
from src.first_rise_breakout.minute_source import SameDayMinutePeakSource
from src.first_rise_breakout.raw_replay import replay_market

CONFIG=FirstRiseRuntimeConfig.from_row(('Y','09:01','15:00','09:01','15:00','10000000','10000000','100000000'))
def at(h,m):return datetime(2026,10,2,h,m)
def bar(h,m,high,low=None):return MinuteBar(at(h,m),Decimal(high),Decimal(high),Decimal(low or high),Decimal(high))


@unittest.skipUnless(os.getenv('FIRST_RISE_TEMP_PG_ENV'),'requires TEMP PostgreSQL')
class JRuntimePostgresTests(unittest.TestCase):
    tearDown=raw_fixture.PgRawTests.tearDown

    def setUp(self):
        raw_fixture.PgRawTests.setUp(self)
        self.c.execute('CREATE TEMP TABLE first_rise_breakout_candidate_event(candidate_event_id uuid PRIMARY KEY,discovered_at timestamp)')
        sql=(Path(__file__).resolve().parents[1]/'database/migrations/20261002_first_rise_j_market.sql').read_text(encoding='utf-8')
        self.c.execute(sql.replace('BEGIN;','').replace('COMMIT;','').replace('CREATE TABLE ','CREATE TEMP TABLE '))
        self.c.execute('CREATE TEMP TABLE first_rise_j_capital_binding(trade_id uuid PRIMARY KEY)')
        self.c.execute('CREATE TEMP TABLE live_broker_order(broker_order_id uuid PRIMARY KEY)')
        self.c.execute('CREATE TEMP TABLE common_code(group_cd text,code text,use_yn text,attr1 text,attr2 text,attr3 text,attr4 text,attr5 text,attr6 text,attr7 text)')
        self.c.execute("INSERT INTO common_code VALUES('FIRST_RISE_CAPACITY','DEFAULT','Y','10','10000000','30','100','3','5','10')")
        sql=(Path(__file__).resolve().parents[1]/'database/migrations/20261004_first_rise_v2_capacity.sql').read_text(encoding='utf-8')
        self.c.execute(sql.replace('BEGIN;','').replace('COMMIT;','').replace('CREATE TABLE ','CREATE TEMP TABLE '))
        self.c.commit()

    def test_runtime_raw_first_stop_second_and_restart(self):
        candidate=CandidateState(uuid4(),at(9,1).date(),'123456',ResearchState.DISCOVERED);discovered=at(9,1)
        self.c.execute('INSERT INTO first_rise_breakout_candidate_event VALUES(%s,%s)',(candidate.candidate_event_id,discovered))
        self.c.commit()
        bars=[bar(9,0,1030),bar(9,1,1025,1000),bar(9,9,1031,1025)]
        def collect(**kw):
            return [dict(bar_time=b.bar_time,stock_code='123456',open_price=b.open_price,
                high_price=b.high_price,low_price=b.low_price,close_price=b.close_price,volume=100,
                previous_close_price=1000,accumulated_amount=None,raw_payload={}) for b in bars]
        source=SameDayMinutePeakSource(SimpleNamespace(collect=collect),raw_repository=self.repo)
        repository=JMarketRepository(self.repo.pool)
        runtime=JMarketRuntime(repository=repository,minute_source=source)
        runtime.register(candidate,discovered_at=discovered)
        runtime.refresh(at=at(9,10),config=CONFIG)
        bars.extend([bar(9,10,1032,1020),bar(9,50,1040),bar(9,51,1035,1010),bar(10,0,1041,1035)])
        runtime.refresh(at=at(10,1),config=CONFIG)
        self.c.commit()
        def signals():
            return self.c.execute('SELECT signal_sequence,entry_signal_time,exit_reason FROM first_rise_j_market_signal ORDER BY signal_sequence').fetchall()
        stored=signals()
        self.assertEqual(stored,[(1,at(9,9),'STOP_ENTRY_BREAK'),(2,at(10,0),None)])
        restarted=JMarketRuntime(repository=repository,minute_source=source)
        restarted.restore(at=at(10,1));restarted.refresh(at=at(10,1),config=CONFIG)
        self.assertEqual(signals(),stored)
        raw=self.repo.load(stock_code='123456',as_of=at(10,1))
        _,steps=replay_market(candidate=candidate,discovered_at=discovered,rows=raw,config=CONFIG)
        entries=[(s.state.sequence,s.market_entry.signal_time) for s in steps if s.market_entry]
        self.assertEqual(entries,[(r[0],r[1]) for r in stored])
        self.assertEqual([s.market_exit.reason for s in steps if s.market_exit],['STOP_ENTRY_BREAK'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM first_rise_j_paper_trade').fetchone()[0],2)


if __name__=='__main__':unittest.main()
