import inspect
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.minute_ma.contracts import Axis, MinuteBar, MinuteMaPath
from src.minute_ma.engine import PreparedMaPoint, SignalEvent, SignalType
from src.minute_ma.v1_live_runtime import MinuteMaV1LiveRuntime, MinuteMaV1LiveStopMonitor
from src.minute_ma.v1_policy import SHORT_POLICY
from src.minute_ma.v1_runtime import MinuteMaV1PaperRuntime


def ds003883_path():
    return MinuteMaPath(
        3883,"V1|DS003883",Axis.KRX_CONTINUOUS,"000660","0193L0","SHORT",
        3,5,10,30,None,"DS003883",3883,SHORT_POLICY)


def exit_fixture():
    path=ds003883_path()
    source=datetime(2026,9,1,9,28)
    point=PreparedMaPoint(
        source,{10:Decimal("256075"),30:Decimal("256066.666667")},
        {10:Decimal("255925"),30:Decimal("256116.666667")},Decimal("256075"),
        finalized_at=datetime(2026,9,1,9,29,0,86118))
    event=SignalEvent(
        path.minute_path_id,path.path_key,SignalType.EXIT,source,point.finalized_at,
        "d"*64,True,point.current,point.previous,"KIS_H0STCNT0_REALTIME")
    return path,point,event


class FixedEngine:
    def __init__(self,point,event):self.point=point;self.event=event
    def prepare(self,**kwargs):return (self.point,)
    def evaluate_prepared(self,**kwargs):return (self.event,)


class ExitPendingRepository:
    def __init__(self, *, proxy_ready=False, trade_count=7):
        self.path,self.point,self.event=exit_fixture()
        self.proxy_ready=proxy_ready
        self.cursor=datetime(2026,9,1,9,27)
        self.pending=[]
        self.closed=set()
        self.trades=[SimpleNamespace(
            minute_policy_paper_trade_id=n,
            entry_execution_time=datetime(2026,9,1,9,1+n),
            underlying_entry_reference_price=Decimal("999999"))
            for n in range(1,trade_count+1)]

    def v1_policy_paths(self):return (self.path,)
    def v1_source_bars(self,**kwargs):return ()
    def v1_runtime_cursor(self,**kwargs):return self.cursor
    def advance_v1_cursor(self,*,last_source_bar_time,**kwargs):self.cursor=last_source_bar_time
    def v1_open_trades(self,**kwargs):
        return tuple(t for t in self.trades if t.minute_policy_paper_trade_id not in self.closed)
    def v1_realtime_bar(self,*,stock_code,at):
        return MinuteBar(at,Decimal("100"),Decimal("100"),Decimal("100"),Decimal("100")) \
            if self.proxy_ready else None
    def v1_pending_exits(self,**kwargs):return tuple(self.pending)
    def v1_defer_normal_exit(self,*,path,event,proxy_bar_time):
        if not self.pending:
            self.pending.append(SimpleNamespace(
                pending_exit_id=1,minute_policy_path_id=path.minute_policy_path_id,
                exit_type="NORMAL_EXIT",event=event,proxy_bar_time=proxy_bar_time,
                trade=None,trigger_underlying_close=None))
        return 1
    def v1_close_normal(self,*,execution_bar,pending_exit_id=None,**kwargs):
        eligible=[t for t in self.trades
                  if t.minute_policy_paper_trade_id not in self.closed
                  and t.entry_execution_time<=execution_bar.bar_time]
        self.closed.update(t.minute_policy_paper_trade_id for t in eligible)
        if pending_exit_id is not None:self.pending.clear()
        return len(eligible)


class MinuteV1PaperExitPendingTest(unittest.TestCase):
    def run_runtime(self,repo):
        runtime=MinuteMaV1PaperRuntime(repo,engine=FixedEngine(repo.point,repo.event))
        return runtime.run_day(trading_date=datetime(2026,9,1).date())

    def test_ds003883_immediate_proxy_closes_all_seven_open_trades(self):
        repo=ExitPendingRepository(proxy_ready=True)
        self.assertLessEqual(repo.point.previous[10],repo.point.previous[30])
        self.assertGreater(repo.point.current[10],repo.point.current[30])
        result=self.run_runtime(repo)
        self.assertEqual((result.normal_exits,len(repo.closed),len(repo.pending)),(7,7,0))

    def test_ds003883_late_proxy_survives_cursor_and_restart_exactly_once(self):
        repo=ExitPendingRepository(proxy_ready=False)
        first=self.run_runtime(repo)
        self.assertEqual((first.normal_exits,len(repo.pending),repo.cursor),
                         (0,1,datetime(2026,9,1,9,28)))
        repo.proxy_ready=True
        second=self.run_runtime(repo)
        third=self.run_runtime(repo)
        self.assertEqual((second.normal_exits,third.normal_exits,len(repo.closed),len(repo.pending)),
                         (7,0,7,0))

    def test_permanently_missing_proxy_stays_pending_without_fake_close(self):
        repo=ExitPendingRepository(proxy_ready=False)
        self.run_runtime(repo)
        self.run_runtime(repo)
        self.assertEqual((len(repo.pending),len(repo.closed)),(1,0))

    def test_delayed_stop_is_pending_and_resolves_trade_specific(self):
        path=ds003883_path()
        trade=SimpleNamespace(minute_policy_paper_trade_id=9,
            entry_execution_time=datetime(2026,8,31,9,1),
            underlying_entry_reference_price=Decimal("100"))
        class Repo:
            ready=False
            pending=[]
            closed=[]
            def v1_open_trades(self,**kwargs):return () if self.closed else (trade,)
            def v1_realtime_bar(self,**kwargs):
                return MinuteBar(kwargs['at'],102,102,102,102) if self.ready else None
            def v1_defer_stop(self,**kwargs):
                if not self.pending:self.pending.append(kwargs)
                return 1
            def v1_close_stop(self,**kwargs):self.closed.append(9);return 1
        repo=Repo();runtime=MinuteMaV1PaperRuntime(repo)
        point=PreparedMaPoint(datetime(2026,9,1,9,0),{},None,Decimal("101"),
                            finalized_at=datetime(2026,9,1,9,1))
        self.assertEqual(runtime._apply_stops((path,),point),0)
        self.assertEqual((len(repo.pending),repo.closed),(1,[]))
        repo.ready=True
        self.assertEqual(runtime._apply_stops((path,),point),1)
        self.assertEqual(repo.closed,[9])

    def test_stop_pending_is_recovered_after_restart(self):
        path=ds003883_path()
        trigger=datetime(2026,9,1,9,0)
        confirmed=datetime(2026,9,1,9,1)
        trade=SimpleNamespace(minute_policy_paper_trade_id=19,
            entry_execution_time=datetime(2026,8,31,9,1),
            underlying_entry_reference_price=Decimal("100"))
        event=SignalEvent(path.minute_path_id,path.path_key,SignalType.EXIT,trigger,confirmed,
                          "e"*64,True,{}, {},"KIS_H0STCNT0_REALTIME")
        pending_fixture=SimpleNamespace(pending_exit_id=3,
            minute_policy_path_id=path.minute_policy_path_id,exit_type="STOP_EXIT",
            event=event,proxy_bar_time=datetime(2026,9,1,9,1),trade=trade,
            trigger_underlying_close=Decimal("101"))
        class Repo:
            closed=[]
            pending=[pending_fixture]
            def v1_policy_paths(self):return (path,)
            def v1_pending_exits(self,**kwargs):return tuple(self.pending)
            def v1_pending_entries(self,**kwargs):return ()
            def v1_realtime_bar(self,**kwargs):return MinuteBar(kwargs['at'],90,90,90,90)
            def v1_close_stop(self,*,pending_exit_id,**kwargs):
                self.closed.append(kwargs['trade'].minute_policy_paper_trade_id)
                self.pending.clear();return 1
            def v1_source_bars(self,**kwargs):return ()
            def v1_runtime_cursor(self,**kwargs):return trigger
            def v1_open_trades(self,**kwargs):return ()
        class EmptyEngine:
            def prepare(self,**kwargs):return ()
            def evaluate_prepared(self,**kwargs):return ()
        repo=Repo()
        result=MinuteMaV1PaperRuntime(repo,engine=EmptyEngine()).run_day(
            trading_date=trigger.date())
        self.assertEqual((result.stop_exits,repo.closed,len(repo.pending)),(1,[19],0))

    def test_live_actual_exit_does_not_wait_for_next_minute_proxy(self):
        runtime_source=inspect.getsource(MinuteMaV1LiveRuntime.run_day)
        stop_source=inspect.getsource(MinuteMaV1LiveStopMonitor.evaluate_completed_bar)
        self.assertNotIn("v1_realtime_bar",runtime_source)
        self.assertNotIn("v1_realtime_bar",stop_source)
        self.assertIn("current_price",runtime_source)
        self.assertIn("current_price",stop_source)


class MinuteV1PaperExitPendingMigrationTest(unittest.TestCase):
    def test_additive_pending_exit_contract(self):
        sql=Path("database/migrations/20260901_minute_v1_paper_exit_pending_additive.sql").read_text(
            encoding="utf-8")
        for token in ("minute_ma_policy_paper_pending_exit","NORMAL_EXIT","STOP_EXIT",
                      "target_paper_trade_id","EXECUTION_PROXY_MISSING",
                      "ux_minute_ma_policy_pending_stop_trade"):
            self.assertIn(token,sql)
        self.assertNotIn("UPDATE minute_ma_policy_paper_trade",sql)
        self.assertNotIn("INSERT INTO minute_ma_policy_paper_event",sql)


if __name__=="__main__":unittest.main()
