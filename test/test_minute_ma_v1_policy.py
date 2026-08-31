import unittest
from datetime import datetime,time
from decimal import Decimal
from types import SimpleNamespace

from src.minute_ma.contracts import Axis,MinuteMaPath,MinuteBar
from src.minute_ma.engine import PreparedMaPoint
from src.minute_ma.v1_policy import LONG_POLICY,SHORT_POLICY,paper_stop_execution_time
from src.minute_ma.v1_runtime import MinuteMaV1PaperRuntime
from src.minute_ma.v1_live_runtime import MinuteMaV1LiveStopMonitor,V1LiveOpenTrade
from src.minute_ma.v1_live_nosend import MinuteMaV1LiveNoSendRuntime
from src.minute_ma.engine import SignalEvent,SignalType


def path(direction="SHORT",policy_path_id=1):
    return MinuteMaPath(10,f"V1|{direction}",Axis.KRX_CONTINUOUS,"000660","0197X0",direction,
                        3,5,3,5,None,"DS",policy_path_id,
                        SHORT_POLICY if direction=="SHORT" else LONG_POLICY)


class FakePaperRepository:
    def __init__(self,trades):self.trades=list(trades);self.stop_calls=[]
    def v1_open_trades(self,*,path):return tuple(x for x in self.trades if x.open)
    def execution_bar(self,*,stock_code,at):return MinuteBar(at,90,91,89,90)
    def v1_close_stop(self,*,path,trade,trigger_bar_time,trigger_underlying_close,execution_bar):
        if not trade.open:return 0
        trade.open=False;self.stop_calls.append(trade.minute_policy_paper_trade_id);return 1


class MinuteMaV1PolicyTest(unittest.TestCase):
    def test_frozen_windows(self):
        self.assertTrue(SHORT_POLICY.allows_entry(datetime(2026,8,28,9,59),live=False))
        self.assertFalse(SHORT_POLICY.allows_entry(datetime(2026,8,28,9,30),live=True))
        self.assertTrue(LONG_POLICY.allows_entry(datetime(2026,8,28,15,18),live=True))
        self.assertFalse(LONG_POLICY.allows_entry(datetime(2026,8,28,13,59),live=False))

    def test_short_and_long_stop_direction(self):
        self.assertTrue(SHORT_POLICY.stop_triggered(anchor=Decimal("100"),completed_underlying_close=Decimal("101")))
        self.assertFalse(SHORT_POLICY.stop_triggered(anchor=Decimal("100"),completed_underlying_close=Decimal("100.99")))
        self.assertTrue(LONG_POLICY.stop_triggered(anchor=Decimal("100"),completed_underlying_close=Decimal("95")))
        self.assertFalse(LONG_POLICY.stop_triggered(anchor=Decimal("100"),completed_underlying_close=Decimal("95.01")))

    def test_validation_proxy_is_strict_next_minute(self):
        trigger=datetime(2026,8,28,9,20)
        self.assertEqual(paper_stop_execution_time(trigger),datetime(2026,8,28,9,21))

    def test_trade_specific_stop_and_restart_duplicate_zero(self):
        trades=[SimpleNamespace(minute_policy_paper_trade_id=1,
             entry_execution_time=datetime(2026,8,27,9,1),underlying_entry_reference_price=Decimal("100"),open=True),
            SimpleNamespace(minute_policy_paper_trade_id=2,
             entry_execution_time=datetime(2026,8,27,9,2),underlying_entry_reference_price=Decimal("110"),open=True)]
        repo=FakePaperRepository(trades);runtime=MinuteMaV1PaperRuntime(repo)
        point=PreparedMaPoint(datetime(2026,8,28,9,0),{},None,102)
        self.assertEqual(runtime._apply_stops([path()],point),1)
        self.assertEqual(repo.stop_calls,[1]);self.assertTrue(trades[1].open)
        # Fresh runtime over the same durable repository cannot close trade 1 again.
        self.assertEqual(MinuteMaV1PaperRuntime(repo)._apply_stops([path()],point),0)

    def test_next_day_gap_uses_original_anchor(self):
        trade=SimpleNamespace(minute_policy_paper_trade_id=7,
            entry_execution_time=datetime(2026,8,27,15,19),
            underlying_entry_reference_price=Decimal("100"),open=True)
        repo=FakePaperRepository([trade]);runtime=MinuteMaV1PaperRuntime(repo)
        self.assertEqual(runtime._apply_stops([path("LONG")],
            PreparedMaPoint(datetime(2026,8,28,9,0),{},None,94)),1)
        self.assertEqual(repo.stop_calls,[7])

    def test_v1_runtime_never_calls_legacy_eod(self):
        import inspect
        source=inspect.getsource(MinuteMaV1PaperRuntime)
        self.assertNotIn("close_eod",source)
        self.assertNotIn("EOD_1519",source)

    def test_paper_anchor_is_underlying_open_at_entry_proxy_minute(self):
        p=path("LONG");point=PreparedMaPoint(datetime(2026,8,28,14,0),{3:2,5:1},{3:0,5:1},100)
        event=SignalEvent(p.minute_path_id,p.path_key,SignalType.ENTRY,point.bar_time,
            datetime(2026,8,28,14,1,1),'d'*64,True,{3:2,5:1},{3:0,5:1})
        class Engine:
            def prepare(self,**kwargs):return (point,)
            def evaluate_prepared(self,**kwargs):return (event,)
        class Repo:
            anchor=None
            def v1_policy_paths(self,**kwargs):return (p,)
            def source_bars(self,**kwargs):return ()
            def v1_runtime_cursor(self,**kwargs):return datetime(2026,8,28,13,59)
            def v1_open_trades(self,**kwargs):return ()
            def execution_bar(self,*,at,**kwargs):return MinuteBar(at,90,91,89,90)
            def underlying_bar(self,*,at,**kwargs):return MinuteBar(at,123,124,122,123)
            def v1_open_trade(self,*,underlying_entry_reference_price,**kwargs):self.anchor=underlying_entry_reference_price;return 1
            def advance_v1_cursor(self,**kwargs):pass
            def v1_close_normal(self,**kwargs):return 0
        repo=Repo();runtime=MinuteMaV1PaperRuntime(repo);runtime.engine=Engine()
        result=runtime.run_day(trading_date=datetime(2026,8,28).date())
        self.assertEqual(result.entries_created,1);self.assertEqual(repo.anchor,Decimal('123'))

    def test_overnight_trade_can_close_on_next_day_normal_ma_exit(self):
        p=path("LONG");point=PreparedMaPoint(datetime(2026,8,28,9,10),{3:0,5:1},{3:2,5:1},99)
        event=SignalEvent(p.minute_path_id,p.path_key,SignalType.EXIT,point.bar_time,
            datetime(2026,8,28,9,11,1),'f'*64,True,{3:0,5:1},{3:2,5:1})
        class Engine:
            def prepare(self,**kwargs):return (point,)
            def evaluate_prepared(self,**kwargs):return (event,)
        class Repo:
            closed=0
            def v1_policy_paths(self,**kwargs):return (p,)
            def source_bars(self,**kwargs):return ()
            def v1_runtime_cursor(self,**kwargs):return datetime(2026,8,27,15,30)
            def v1_open_trades(self,**kwargs):return ()
            def execution_bar(self,*,at,**kwargs):return MinuteBar(at,90,91,89,90)
            def v1_close_normal(self,**kwargs):self.closed+=1;return 1
            def advance_v1_cursor(self,**kwargs):pass
        repo=Repo();runtime=MinuteMaV1PaperRuntime(repo);runtime.engine=Engine()
        result=runtime.run_day(trading_date=datetime(2026,8,28).date())
        self.assertEqual((result.normal_exits,repo.closed),(1,1))

    def test_late_proxy_resolves_durable_pending_exactly_once(self):
        p=path("LONG");point=PreparedMaPoint(datetime(2026,8,28,14,0),{3:2,5:1},{3:0,5:1},100)
        event=SignalEvent(p.minute_path_id,p.path_key,SignalType.ENTRY,point.bar_time,
            datetime(2026,8,28,14,1,1),'a'*64,True,{3:2,5:1},{3:0,5:1})
        class Engine:
            def prepare(self,**kwargs):return (point,)
            def evaluate_prepared(self,**kwargs):return (event,)
        class Repo:
            cursor=datetime(2026,8,28,13,59);pending=[];proxy_ready=False;created=0
            def v1_policy_paths(self,**kwargs):return (p,)
            def source_bars(self,**kwargs):return ()
            def v1_runtime_cursor(self,**kwargs):return self.cursor
            def v1_open_trades(self,**kwargs):return ()
            def v1_pending_entries(self,**kwargs):return tuple(self.pending)
            def execution_bar(self,*,at,**kwargs):return MinuteBar(at,90,91,89,90) if self.proxy_ready else None
            def underlying_bar(self,*,at,**kwargs):return MinuteBar(at,123,124,122,123) if self.proxy_ready else None
            def v1_defer_entry(self,*,path,event,proxy_bar_time,pending_reason):
                if not self.pending:self.pending.append(SimpleNamespace(
                    pending_entry_id=1,minute_policy_path_id=path.minute_policy_path_id,
                    event=event,proxy_bar_time=proxy_bar_time))
            def v1_open_trade(self,*,pending_entry_id=None,**kwargs):
                if pending_entry_id is not None and self.pending:
                    self.pending.clear();self.created+=1;return 1
                return 0
            def advance_v1_cursor(self,*,last_source_bar_time,**kwargs):self.cursor=last_source_bar_time
            def v1_close_normal(self,**kwargs):return 0
        repo=Repo();runtime=MinuteMaV1PaperRuntime(repo);runtime.engine=Engine()
        first=runtime.run_day(trading_date=datetime(2026,8,28).date())
        self.assertEqual((first.entries_created,first.rejected_entries,len(repo.pending)),(0,1,1))
        repo.proxy_ready=True
        second=runtime.run_day(trading_date=datetime(2026,8,28).date())
        third=runtime.run_day(trading_date=datetime(2026,8,28).date())
        self.assertEqual((second.entries_created,third.entries_created,repo.created,len(repo.pending)),(1,0,1,0))


class MinuteMaV1LiveStopTest(unittest.TestCase):
    def test_live_stop_targets_one_trade_ownership(self):
        trades=[V1LiveOpenTrade(9,1,"OWN-9",Decimal("100"),datetime(2026,8,27,9,1)),
                V1LiveOpenTrade(10,1,"OWN-10",Decimal("110"),datetime(2026,8,27,9,2))]
        class Repo:
            def v1_live_open_trades(self,*,path):return trades
        class Planner:
            calls=[]
            def plan_trade_exit(self,**kwargs):self.calls.append(kwargs["minute_live_trade_id"]);return "NO_SEND_VALIDATED"
        planner=Planner();price=SimpleNamespace(current_price=lambda code:Decimal("1"))
        result=MinuteMaV1LiveStopMonitor(repository=Repo(),planner=planner,price_lookup=price).evaluate_completed_bar(
            path=path(),bar=MinuteBar(datetime(2026,8,28,9,0),102,102,102,102))
        self.assertEqual(result,{"NO_SEND_VALIDATED":1});self.assertEqual(planner.calls,[9])

    def test_live_nosend_bootstrap_and_duplicate_zero(self):
        p=path("SHORT")
        point=PreparedMaPoint(datetime(2026,8,28,9,10),{3:2,5:1},{3:0,5:1},100)
        event=SignalEvent(p.minute_path_id,p.path_key,SignalType.ENTRY,point.bar_time,
                          datetime(2026,8,28,9,11,1),'e'*64,True,{3:2,5:1},{3:0,5:1})
        class Engine:
            def prepare(self,**kwargs):return (point,)
            def evaluate_prepared(self,**kwargs):return (event,)
        class Repo:
            cursor=None
            def v1_policy_paths(self,**kwargs):return (p,)
            def source_bars(self,**kwargs):return ()
            def v1_live_runtime_cursor(self,**kwargs):return self.cursor
            def advance_v1_live_cursor(self,*,last_source_bar_time,**kwargs):self.cursor=last_source_bar_time
            def v1_live_open_trades(self,**kwargs):return ()
        class Adapter:
            seen=set();calls=0
            def plan_entry(self,**kwargs):
                if kwargs['signal_event_key'] not in self.seen:self.calls+=1;self.seen.add(kwargs['signal_event_key'])
                return SimpleNamespace(status='NO_SEND_VALIDATED')
        repo=Repo();adapter=Adapter();lookup=SimpleNamespace(
            current_price=lambda code:Decimal('100'),minute_open=lambda code,bar_time:Decimal('100'))
        cash=SimpleNamespace(orderable_cash=lambda **kwargs:SimpleNamespace(amount=Decimal('1000000')))
        runtime=MinuteMaV1LiveNoSendRuntime(repository=repo,adapter=adapter,
            execution_price_lookup=lookup,underlying_price_lookup=lookup,cash_lookup=cash)
        runtime.engine=Engine()
        self.assertEqual(runtime.run_day(trading_date=datetime(2026,8,28).date()),{'BOOTSTRAPPED_NO_REPLAY':1})
        repo.cursor=datetime(2026,8,28,9,9)
        self.assertEqual(runtime.run_day(trading_date=datetime(2026,8,28).date()),{'NO_SEND_VALIDATED':1})
        repo.cursor=datetime(2026,8,28,9,9)  # simulated restart replay
        runtime.run_day(trading_date=datetime(2026,8,28).date())
        self.assertEqual(adapter.calls,1)


if __name__=="__main__":unittest.main()
