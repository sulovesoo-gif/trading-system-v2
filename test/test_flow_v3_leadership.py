"""No .env, no broker. PG tests require explicit isolated localhost opt-in."""
import ast
import json
import os
import re
import unittest
from dataclasses import replace,asdict
from datetime import date,datetime,time,timedelta
from decimal import Decimal as D
from pathlib import Path

from src.flow_v3.models import MinuteBase,StrategyContract
from src.flow_v3.engine import FlowV3SignalEngine,PAIR_CODE
from src.flow_v3_leadership.research import (Session,Prices,build_states,entry_matches,extended_trades,
    replay,unavailable,rank_rows,POLICIES,PERIODS,STOCK_COST,STOCK_SELL_TAX)
from src.flow_v3_leadership.source import regular_prices

ROOT=Path(__file__).resolve().parents[1]
DAY=date(2026,9,14)
def at(h,m,day=DAY):return datetime.combine(day,time(h,m))
def strategy(sid='FV3000001',family='F1',exit_policy='SIGNAL_EOD',program='P0'):
    return StrategyContract(sid,'000660','LONG',family,1,3,program,1,3,exit_policy,'0193T0')
def base(t,value=1):
    return MinuteBase(t.date(),'000660',t,D(100),D(value),D(0),D(value),D(-1),
                      D(10),D(11),D(10),D(11),snapshot_count=12)
def state(t,cross=1):
    s=build_states([base(t)])[0]
    return replace(s,velocity_value=D(1),flow_crosses={'01':cross},velocity_crosses={'01':cross},
                   long_absorption=True,program_velocity=D(-1))
def trade(tid,entry,exit,ep=100,xp=110,reason='NORMAL_EXIT'):
    return dict(paper_trade_id=tid,entry_signal_time=entry-timedelta(minutes=1),
        entry_execution_time=entry,entry_execution_price=D(ep),actual_exit_time=exit,
        actual_exit_price=D(xp) if exit else None,exit_reason=reason)


class LeadershipTests(unittest.TestCase):
    def test_regular_cutoff(self):
        s=Session();self.assertTrue(s.allowed(at(15,18),'REGULAR','ENTRY'))
        self.assertFalse(s.allowed(at(15,19),'REGULAR','ENTRY'))

    def test_extended_exit_regular_entry_only(self):
        s=Session();self.assertFalse(s.allowed(at(16,1),'EXTENDED_EXIT','ENTRY'))
        self.assertTrue(s.allowed(at(16,1),'EXTENDED_EXIT','EXIT'))
        self.assertTrue(s.allowed(at(15,29),'REGULAR','EXIT','SIGNAL_HOLD'))
        self.assertFalse(s.allowed(at(15,29),'REGULAR','EXIT','SIGNAL_EOD'))
        trades,issues=extended_trades(strategy(),[state(at(15,18))],
            Prices([(at(16,0),100)]),'EXTENDED_EXIT',s,DAY)
        self.assertFalse(trades);self.assertIn('MISSING_ENTRY_PRICE',issues)

    def test_extended_full(self):
        for action in ('ENTRY','EXIT'):
            self.assertTrue(Session().allowed(at(19,59),'EXTENDED_FULL',action))
            self.assertFalse(Session().allowed(at(20,0),'EXTENDED_FULL',action))

    def test_after_eligibility(self):
        for action in ('ENTRY','EXIT'):
            self.assertFalse(Session().allowed(at(10,0),'AFTER',action))
            self.assertTrue(Session().allowed(at(16,0),'AFTER',action))

    def test_no_16_reset(self):
        states=build_states([base(at(15,58),10),base(at(15,59),20),base(at(16,0),30)])
        self.assertEqual(states[-1].flow_averages[3],20)
        self.assertEqual(states[-1].velocity_value,10)

    def test_new_day_reset(self):
        rows=build_states([base(at(19,59),100),base(at(9,0,DAY+timedelta(days=1)),1)])
        self.assertIsNone(rows[-1].velocity_value);self.assertIsNone(rows[-1].flow_averages[3])

    def test_missing_minute_no_fabrication(self):
        rows=build_states([base(at(15,30)),base(at(16,0))])
        self.assertEqual(len(rows),2);self.assertIsNone(rows[-1].velocity_value)

    def test_regular_signal_parity_all_families_programs(self):
        now=state(at(10,0));recent=[state(at(9,59))];engine=FlowV3SignalEngine()
        for family in ('F1','F2','F3','F4'):
            for program in ('P0','P1','P2'):
                s=strategy(family=family,program=program)
                self.assertEqual(entry_matches(s,now,recent),bool(engine.entry_signals(state=now,recent_states=recent,strategies=[s])))

    def test_six_million_and_cost(self):
        m=replay([trade(1,at(10,1),at(11,1),250000,260000)],6000000,DAY)['daily']
        self.assertEqual(m['maximum_quantity'],24)
        self.assertEqual(m['net_profit'],24*(D(10000)-D(510000)*STOCK_COST-D(260000)*STOCK_SELL_TAX))

    def test_independent_capital_axes_integer_rounding(self):
        t=[trade(1,at(10,1),at(11,1),400000,410000)]
        a=replay(t,5000000,DAY)['daily'];b=replay(t,6000000,DAY)['daily']
        self.assertEqual((a['maximum_quantity'],b['maximum_quantity']),(12,15))
        self.assertNotEqual(a['net_profit']*D('1.2'),b['net_profit'])

    def test_compound_profit_then_new_quantity(self):
        t=[trade(1,at(10,1),at(10,2),100,210),trade(2,at(10,2),at(10,3),100,100)]
        self.assertEqual(replay(t,100,DAY)['daily']['maximum_quantity'],2)

    def test_loss_reduces_quantity(self):
        t=[trade(1,at(10,1),at(10,2),100,50),trade(2,at(10,3),at(10,4),100,100)]
        self.assertEqual(replay(t,100,DAY)['daily']['skipped_quantity_count'],1)

    def test_equal_timestamp_self_causal_and_cost(self):
        m=replay([trade(1,at(10,1),at(10,1))],1000,DAY)['daily']
        self.assertEqual(m['trade_count'],1);self.assertLess(m['net_profit'],100)

    def test_true_reversal_rejected(self):
        self.assertRaises(ValueError,replay,[trade(1,at(10,2),at(10,1))],100,DAY)

    def test_duplicate_trade_rejected(self):
        t=trade(1,at(10,1),at(10,2));self.assertRaises(ValueError,replay,[t,t],100,DAY)

    def test_overlapping_entries_independent(self):
        t=[trade(1,at(10,1),at(11,0)),trade(2,at(10,2),at(11,1))]
        self.assertEqual(replay(t,1000,DAY)['daily']['trade_count'],2)

    def test_hold_carry_and_daily_weekly_monthly(self):
        yesterday=DAY-timedelta(days=1)
        t=[trade(1,at(10,1,yesterday),at(10,1)),trade(2,at(11,1),None)]
        result=replay(t,1000,DAY)
        for m in result.values():
            self.assertEqual(m['overnight_count'],1);self.assertEqual(m['open_count'],1)

    def test_period_boundary_capital(self):
        yesterday=DAY-timedelta(days=1)
        t=[trade(1,at(10,1,yesterday),at(11,1,yesterday)),trade(2,at(10,1),at(11,1))]
        result=replay(t,1000,DAY)
        self.assertGreater(result['daily']['initial_capital'],1000)
        self.assertGreater(result['monthly']['net_profit'],result['daily']['net_profit'])

    def test_eod_reverse_price_search(self):
        p=Prices([(at(15,18),D(100)),(at(15,20),D(200))])
        self.assertEqual(p.eod(DAY,time(15,19)),(at(15,18),100))

    def test_regular_eod_overlay_preserves_input(self):
        t=trade(1,at(10,1),at(15,29));t.update(exit_policy_code='SIGNAL_EOD',normal_exit_signal_time=None)
        p=Prices([(at(10,1),100),(at(15,19),120),(at(15,29),130)])
        rows,issues=regular_prices([t],p,DAY,Session())
        self.assertEqual(rows[0]['actual_exit_time'],at(15,19));self.assertEqual(t['actual_exit_time'],at(15,29));self.assertFalse(issues)

    def test_missing_price_is_not_zero_profit(self):
        t=trade(1,at(10,1),at(11,1));t['exit_policy_code']='SIGNAL_HOLD'
        rows,issues=regular_prices([t],Prices([]),DAY,Session())
        self.assertFalse(rows);self.assertIn('MISSING_ENTRY_PRICE',issues)
        self.assertIsNone(unavailable('MISSING_PRICE')['compound_return'])

    def test_extended_signal_and_eod(self):
        ss=[state(at(16,1)),state(at(16,3),0)]
        p=Prices([(at(16,2),100),(at(19,59),110)])
        rows,issues=extended_trades(strategy(),ss,p,'AFTER',Session(),DAY)
        self.assertFalse(issues);self.assertEqual(rows[0]['actual_exit_time'],at(19,59))
        self.assertEqual(rows[0]['exit_reason'],'SIGNAL_EOD')

    def test_hold_normal_exit_next_day(self):
        tomorrow=DAY+timedelta(days=1)
        ss=[state(at(16,1)),state(at(16,1,tomorrow),-1)]
        p=Prices([(at(16,2),100),(at(16,2,tomorrow),110)])
        rows,issues=extended_trades(strategy(exit_policy='SIGNAL_HOLD'),ss,p,'AFTER',Session(),tomorrow)
        self.assertFalse(issues);self.assertEqual(rows[0]['actual_exit_time'],at(16,2,tomorrow))

    def test_f2_exit_uses_velocity(self):
        ss=[state(at(16,1)),replace(state(at(16,3),1),velocity_crosses={'01':-1})]
        p=Prices([(at(16,2),100),(at(16,4),110)])
        rows,issues=extended_trades(strategy(family='F2',exit_policy='SIGNAL_HOLD'),ss,p,'AFTER',Session(),DAY)
        self.assertEqual(rows[0]['actual_exit_time'],at(16,4));self.assertFalse(issues)

    def test_rank_ties_and_unavailable(self):
        rows=[]
        for sid,status in [('FV3000002','COMPLETE'),('FV3000001','COMPLETE'),('FV3000003','MISSING')]:
            r=dict(strategy_id=sid,capital_base=D(6000000))
            for p in POLICIES:
                for w in PERIODS:r[p.lower()+'_'+w]=dict(status=status,compound_return=D(1),net_profit=D(10),rank=None)
            rows.append(r)
        rank_rows(rows)
        self.assertEqual([r['regular_daily']['rank'] for r in rows],[2,1,None])

    def test_prices_ambiguous_source_rejected(self):
        self.assertRaises(ValueError,Prices,[(at(10,1),100),(at(10,1),101)])

    def test_api_bounds_injection(self):
        from src.service.flow_v3_leadership_dashboard_service import parameters
        for q in ({'policy':['x; DROP']},{'top':['9600']},{'capital':['NaN']},{'capital':['abc']}):
            self.assertRaises((ValueError,TypeError),parameters,q)

    def test_no_trading_dependency_or_dml(self):
        files=list((ROOT/'src/flow_v3_leadership').glob('*.py'))+[ROOT/'src/service/flow_v3_leadership_dashboard_service.py']
        for file in files:
            content=file.read_text(encoding='utf-8');ast.parse(content)
            self.assertNotRegex(content,r'(?i)(INSERT INTO|UPDATE|DELETE FROM)\s+(raw_|flow_v3_live_|flow_v3_paper_|flow_v3_strategy_)')
            self.assertNotRegex(content,r'from .*\b(live_repository|live_transport|kis_client|runtime)\b')

    def test_units_independent_and_local_ui(self):
        service=(ROOT/'systemd/trading-flow-v3-leadership.service').read_text()
        self.assertNotIn('PartOf=',service);self.assertNotIn('trading-flow-v3-live',service)
        self.assertNotIn('Restart=',service)
        page=(ROOT/'reports/multi-ma/flow-v3-leadership.html').read_text(encoding='utf-8')
        for value in ('value="50"','value="100"','Rank History','capitalChart','rankChart','policyChart','@media','viewport'):
            self.assertIn(value,page)
        self.assertNotIn('innerHTML',page)


@unittest.skipUnless(os.getenv('LEADERSHIP_TEST_DSN'),'disposable local PostgreSQL opt-in')
class LeadershipDatabaseTests(unittest.TestCase):
    def test_atomic_snapshot_migration_api_and_source(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from src.flow_v3_leadership.source import capitals,universe,bases,prices,session
        from src.flow_v3_leadership.snapshot import publish
        from scripts.research.backfill_flow_v3_leadership import TOP5,plan,ranking_report
        from src.service.flow_v3_leadership_dashboard_service import ranking,history,options
        dsn=os.environ['LEADERSHIP_TEST_DSN'];cfg=conninfo_to_dict(dsn)
        self.assertEqual(cfg.get('host'),'127.0.0.1');self.assertEqual(cfg.get('dbname'),'flow_leadership_test')
        conn=psycopg.connect(dsn,autocommit=True);self.addCleanup(conn.close)
        # Fresh per-test schema; production never used and no existing schemas cleaned.
        import uuid
        schema='leadership_test_'+uuid.uuid4().hex
        conn.execute('CREATE SCHEMA '+schema);conn.execute('SET search_path TO '+schema)
        for filename in ('01_common_code_group.sql','02_common_code.sql'):
            conn.execute((ROOT/'database/ddl'/filename).read_text(encoding='utf-8'))
        migration=(ROOT/'database/migrations/20260915_flow_v3_leadership.sql').read_text(encoding='utf-8')
        conn.execute(migration);conn.execute(migration)
        axes=capitals(conn);self.assertEqual(len(axes),12)
        self.assertEqual([r['amount'] for r in axes if r['is_default']],[6000000])
        # Disabling only this test's code automatically affects both readers.
        conn.execute("UPDATE common_code SET use_yn='N' WHERE group_cd='FLOW_LEADERSHIP_CAPITAL' AND code='5000000'")
        self.assertEqual(len(options(conn)['capitals']),11)
        conn.execute("UPDATE common_code SET use_yn='Y' WHERE group_cd='FLOW_LEADERSHIP_CAPITAL' AND code='5000000'")
        conn.execute("UPDATE common_code SET use_yn='N' WHERE group_cd='FLOW_LEADERSHIP_CAPITAL' AND code='6000000'")
        self.assertEqual([r['amount'] for r in capitals(conn) if r['is_default']],[5000000])
        conn.execute("UPDATE common_code SET use_yn='Y' WHERE group_cd='FLOW_LEADERSHIP_CAPITAL' AND code='6000000'")
        rows=[]
        for i in range(105):
            sid=TOP5[i] if i<len(TOP5) else 'FV3'+str(i).zfill(6)
            row=dict(snapshot_date=DAY,strategy_id=sid,capital_base=D(6000000),stock_code='000660',direction='LONG',
                     strategy_definition=asdict(strategy(sid,exit_policy='SIGNAL_HOLD')))
            for p in POLICIES:
                for w,m in replay([trade(1,at(10,1),at(11,1),100,100+i)],6000000,DAY).items():row[p.lower()+'_'+w]=m
            rows.append(row)
        rank_rows(rows);audit={'fixture':True}
        self.assertEqual(publish(conn,rows,audit,DAY)['status'],'INSERTED')
        self.assertEqual(publish(conn,rows,audit,DAY)['status'],'UNCHANGED')
        self.assertRaises(ValueError,publish,conn,rows,{'changed':True},DAY)
        from unittest.mock import patch
        with patch('src.flow_v3_leadership.snapshot.VERSION','LEADERSHIP_V1_STOCK_COST_09A08'):
            self.assertRaisesRegex(ValueError,'IMMUTABLE_SNAPSHOT_CONFLICT',publish,conn,rows,audit,DAY)
        report=ranking_report(conn,DAY)
        for period in PERIODS:
            self.assertEqual(len(report['periods'][period]['top50']),50)
            self.assertEqual(len(report['periods'][period]['live_top5']),5)
        q={'date':[str(DAY)],'capital':['6000000']}
        self.assertEqual(len(ranking(conn,q)['rows']),50)
        self.assertEqual(len(ranking(conn,{**q,'top':['100']})['rows']),100)
        first=ranking(conn,q)['rows'][0];self.assertEqual(first['rank_label'],'NEW')
        self.assertEqual(len(history(conn,{**q,'strategies':[first['strategy_id']]})['rows']),1)
        self.assertFalse(ranking(conn,{**q,'direction':['SHORT']})['rows'])
        self.assertRaises(ValueError,ranking,conn,{**q,'sort':['injection']})
        for row in rows:row['snapshot_date']=DAY+timedelta(days=1)
        publish(conn,rows,audit,DAY)
        latest=ranking(conn,{**q,'date':[str(DAY+timedelta(days=1))]})['rows'][0]
        self.assertEqual(latest['rank_change'],0)
        self.assertEqual(conn.execute('SELECT count(*) FROM flow_v3_leadership_snapshot').fetchone()[0],210)
        # Transaction failure must not leave a manifest visible without its rows.
        bad=[dict(rows[0],snapshot_date=DAY+timedelta(days=2),stock_code='999999')]
        self.assertRaises(psycopg.errors.CheckViolation,publish,conn,bad,audit,DAY)
        self.assertEqual(conn.execute('SELECT count(*) FROM flow_v3_leadership_run').fetchone()[0],2)
        self.assertEqual(conn.execute('SELECT count(*) FROM common_code').fetchone()[0],12)
        conn.execute((ROOT/'test/fixtures/flow_v3_leadership_source.sql').read_text(encoding='utf-8'))
        self.assertEqual(len(universe(conn)),1)
        work=plan(conn,DAY)
        self.assertEqual(work['research_start'],DAY)
        self.assertEqual(work['dates'],[DAY]);self.assertFalse(work['blockers'])
        self.assertEqual(session(conn).extended_end,time(20))
        begin,finish=at(9,0),at(20,1)
        p=prices(conn,'000660',begin,finish)
        self.assertEqual(p.values[at(9,1)],250000)  # No venue contamination or duplicate JOIN.
        b=bases(conn,'000660',begin,finish)
        self.assertEqual(len(b),3);self.assertEqual(b[1].flow_value,-750000)
        self.assertEqual(b[1].program_net_flow,20);self.assertEqual(b[1].bid_end,11)
        from src.flow_v3_leadership.snapshot import compute
        before=conn.execute('SELECT * FROM flow_v3_paper_trade').fetchall()
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            computed,audit=compute(conn,DAY,DAY)
        self.assertEqual(len(computed),12)
        m=next(r for r in computed if r['capital_base']==6000000)
        self.assertEqual(m['regular_daily']['maximum_quantity'],24)
        self.assertEqual(m['regular_daily']['trade_count'],1)
        self.assertEqual(m['regular_daily']['status'],'COMPLETE')
        self.assertIn('0.002 sell-side',audit['cost_contract'])
        self.assertEqual(m['extended_full_daily']['status'],'EXTENDED_PRICE_UNAVAILABLE')
        self.assertIsNone(m['extended_full_daily']['compound_return'])
        self.assertEqual(conn.execute('SELECT * FROM flow_v3_paper_trade').fetchall(),before)
        # Snapshot connection restart reads the same immutable result/hash.
        with psycopg.connect(dsn,autocommit=True) as restart:
            restart.execute('SET search_path TO '+schema)
            self.assertEqual(publish(restart,rows,audit={'fixture':True},research_start=DAY)['status'],'UNCHANGED')
        from http.server import ThreadingHTTPServer
        from scripts.dashboard.serve_flow_v3_leadership import handler
        from threading import Thread
        from urllib.request import urlopen
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(dsn+' options=-csearch_path='+schema))
        worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            url='http://127.0.0.1:'+str(server.server_port)
            with urlopen(url+'/flow-v3-leadership.html',timeout=5) as response:
                self.assertEqual(response.status,200)
                self.assertIn('Leadership Ranking',response.read().decode())
            with urlopen(url+'/leadership/api/options',timeout=5) as response:
                self.assertEqual(len(json.load(response)['capitals']),12)
            with urlopen(url+'/leadership/api/ranking?date=2026-09-14&capital=6000000&top=100',timeout=5) as response:
                self.assertEqual(len(json.load(response)['rows']),100)
        finally:
            server.shutdown();server.server_close();worker.join(timeout=5)


if __name__=='__main__':unittest.main()
