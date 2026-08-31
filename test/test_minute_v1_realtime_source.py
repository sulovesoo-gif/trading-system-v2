from __future__ import annotations

import unittest
from datetime import datetime,timedelta
from pathlib import Path

from src.minute_ma.contracts import Axis,MinuteBar,MinuteMaPath
from src.minute_ma.engine import MinuteMaSignalEngine,PreparedMaPoint,SignalType
from src.minute_ma.realtime_dispatch import DispatchWatermark,MinuteV1RealtimeDispatcher
from src.minute_ma.v1_policy import LONG_POLICY


BASE=datetime(2026,8,31,15,0)


def path():
    return MinuteMaPath(1,'V1|LONG',Axis.KRX_CONTINUOUS,'005930','0193W0','LONG',
                        3,5,3,5,None,'DS',1,LONG_POLICY)


class MinuteV1RealtimeSourceTest(unittest.TestCase):
    def test_finalized_at_is_official_confirmation_time(self):
        finalized=BASE+timedelta(minutes=1,milliseconds=247)
        point=PreparedMaPoint(BASE,{3:101,5:100},{3:99,5:100},100,finalized,'KIS_H0STCNT0_REALTIME')
        events=MinuteMaSignalEngine().evaluate_prepared(path=path(),points=(point,))
        self.assertEqual((len(events),events[0].confirmed_at,events[0].signal_source),
                         (1,finalized,'KIS_H0STCNT0_REALTIME'))

    def test_ineligible_bar_cannot_emit_or_bridge_crossover(self):
        bars=[MinuteBar(BASE+timedelta(minutes=i),100,100,100,100) for i in range(5)]
        bars.append(MinuteBar(BASE+timedelta(minutes=5),110,110,110,110,
                              finalized_at=BASE+timedelta(minutes=6),signal_eligible=False,
                              source_name='KIS_H0STCNT0_REALTIME'))
        points=MinuteMaSignalEngine().prepare(path=path(),bars=bars)
        self.assertNotIn(BASE+timedelta(minutes=5),{point.bar_time for point in points})

    def test_same_strategy_source_bar_keeps_one_event_identity(self):
        engine=MinuteMaSignalEngine();p=path()
        a=PreparedMaPoint(BASE,{3:101,5:100},{3:99,5:100},100,BASE+timedelta(minutes=1),'KIS_H0STCNT0_REALTIME')
        b=PreparedMaPoint(BASE,{3:101,5:100},{3:99,5:100},100,BASE+timedelta(minutes=1,seconds=30),'REST_1MIN_LEGACY')
        self.assertEqual(engine.evaluate_prepared(path=p,points=(a,))[0].signal_event_key,
                         engine.evaluate_prepared(path=p,points=(b,))[0].signal_event_key)

    def test_v1_runtime_has_no_rest_source_route(self):
        for filename in ('src/minute_ma/v1_runtime.py','src/minute_ma/v1_live_runtime.py',
                         'src/minute_ma/v1_live_nosend.py'):
            source=Path(filename).read_text(encoding='utf-8')
            self.assertIn('v1_source_bars',source)
            self.assertNotIn('repository.source_bars(',source)

    def test_realtime_proxy_has_no_rest_fallback(self):
        source=Path('src/minute_ma/repository.py').read_text(encoding='utf-8')
        method=source.split('def v1_realtime_bar',1)[1].split('def execution_bar',1)[0]
        self.assertIn('flow_realtime_minute_bar',method)
        self.assertNotIn('raw_stock_minute',method)

    def test_dispatcher_wakes_only_for_krx_signal_session(self):
        source=Path('src/minute_ma/realtime_dispatch.py').read_text(encoding='utf-8')
        self.assertIn("bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'",source)

    def test_dispatcher_bootstraps_without_replay(self):
        watermark=DispatchWatermark(BASE,BASE-timedelta(minutes=1),'005930')
        class Repo:
            CONSUMERS=('V1_PAPER','V1_LIVE')
            boot=[]
            def latest_eligible(self,**kwargs):return watermark
            def cursor_is_empty(self,**kwargs):return True
            def bootstrap_no_replay(self,*,consumer_code,watermark):self.boot.append(consumer_code)
        calls=[];repo=Repo();result=MinuteV1RealtimeDispatcher(
            repo,commands={'V1_PAPER':('paper',),'V1_LIVE':('live',)},runner=lambda command:calls.append(command) or 0).poll_once()
        self.assertEqual(result,{'V1_PAPER':'BOOTSTRAPPED_NO_REPLAY','V1_LIVE':'BOOTSTRAPPED_NO_REPLAY'})
        self.assertEqual(calls,[])

    def test_dispatch_failure_does_not_advance_cursor(self):
        watermark=DispatchWatermark(BASE,BASE-timedelta(minutes=1),'000660')
        class Repo:
            CONSUMERS=('V1_PAPER',);advanced=0
            def latest_eligible(self,**kwargs):return watermark
            def cursor_is_empty(self,**kwargs):return False
            def advance(self,**kwargs):self.advanced+=1
        repo=Repo();result=MinuteV1RealtimeDispatcher(
            repo,commands={'V1_PAPER':('paper',)},runner=lambda command:9).poll_once()
        self.assertEqual(result,{'V1_PAPER':'FAILED_9'});self.assertEqual(repo.advanced,0)

    def test_dispatch_success_advances_exactly_once(self):
        watermark=DispatchWatermark(BASE,BASE-timedelta(minutes=1),'000660')
        class Repo:
            CONSUMERS=('V1_LIVE',);advanced=0
            def latest_eligible(self,**kwargs):return watermark if self.advanced==0 else None
            def cursor_is_empty(self,**kwargs):return False
            def advance(self,**kwargs):self.advanced+=1
        repo=Repo();dispatcher=MinuteV1RealtimeDispatcher(
            repo,commands={'V1_LIVE':('live',)},runner=lambda command:0)
        self.assertEqual(dispatcher.poll_once(),{'V1_LIVE':'DISPATCHED'})
        self.assertEqual(dispatcher.poll_once(),{});self.assertEqual(repo.advanced,1)


if __name__=='__main__':unittest.main()
