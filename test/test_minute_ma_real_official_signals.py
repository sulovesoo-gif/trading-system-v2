"""No network/DB writes: production consumers with recorded/synthetic source fixtures."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.minute_ma.contracts import Axis, MinuteBar, MinuteMaPath
from src.minute_ma.engine import MinuteMaSignalEngine, PreparedMaPoint, SignalType
from src.minute_ma.official_signals import OfficialSignalCycle, signal_evidence
from src.minute_ma.real_live import MinuteMaRealLiveRuntime, RealLiveRoute
from src.minute_ma.real_paper import RealFilter, RealSnapshot
from src.minute_ma.real_paper_runtime import MinuteMaRealPaperRuntime


DAY = date(2026, 9, 30)
AT = datetime(2026, 9, 30, 15, 17)
BEFORE = datetime(2026, 9, 30, 8)


def path(strategy=1981):
    return MinuteMaPath(7921 if strategy==1981 else 8881, f'V1|{strategy}',
        Axis.KRX_CONTINUOUS, '005930', '005930', 'LONG', 5, 10,
        10 if strategy==1981 else 20, 30, None, str(strategy))


def bars(*, crossover=True, end=AT, source='KIS_H0UNCNT0_INTEGRATED'):
    prices=[100.]*50+[110. if crossover else 100.]
    return tuple(MinuteBar(end-timedelta(minutes=50-i),p,p,p,p,1,
        end-timedelta(minutes=50-i)+timedelta(minutes=1,milliseconds=247),True,source)
        for i,p in enumerate(prices))


class Repository:
    def __init__(self, rows): self.rows=rows; self.calls=0
    def v1_source_bars(self, **kwargs):
        assert kwargs['stock_code']=='005930'
        self.calls+=1
        return self.rows


class Pool:
    def __init__(self): self.executed=[]
    @contextmanager
    def connection(self): yield self
    @contextmanager
    def cursor(self): yield self
    def execute(self, sql, params=None): self.executed.append((sql,params))
    def commit(self): pass


class Planner:
    def __init__(self): self.entries=[]; self.exits=[]
    def plan_entry(self, **kwargs):
        self.entries.append(kwargs); return 'READY_FOR_BROKER'
    def has_open_trade(self, **kwargs): return True
    def plan_exit(self, **kwargs):
        self.exits.append(kwargs); return {'READY_FOR_BROKER':1}


def route(strategy=1981, filter_code=RealFilter.BASE, **kwargs):
    result=RealLiveRoute(strategy,strategy,filter_code,path(strategy),'LEVERAGE','0193W0',
        'FIXED_QTY',Decimal(0),1,1,BEFORE,BEFORE,True,BEFORE)
    return replace(result,**kwargs)


def live(rows, routes=None, snapshot=None):
    repository=Repository(rows); signals=OfficialSignalCycle(repository); planner=Planner()
    quoted=[]
    def price(code): quoted.append(code); return Decimal(100)
    runtime=MinuteMaRealLiveRuntime(pool=Pool(),planner=planner,
        price_lookup=SimpleNamespace(current_price=price),
        cash_lookup=SimpleNamespace(orderable_cash=lambda **kw:SimpleNamespace(amount=Decimal(1000))),
        signals=signals)
    runtime._routes=lambda: tuple(routes or [route(1981),route(2221)])
    runtime._real=lambda *args: {AT:snapshot} if snapshot else {}
    return runtime,planner,repository,quoted


def test_1981_2221_krx_only_cross_never_creates_entry_or_broker_request():
    krx=bars(source='REST_1MIN_LEGACY')
    assert all(any(e.signal_type is SignalType.ENTRY for e in
        MinuteMaSignalEngine().evaluate(path=path(s),bars=krx)) for s in (1981,2221))
    runtime,planner,repository,quoted=live(bars(crossover=False))
    assert runtime.run_day(trading_date=DAY)=={}
    assert planner.entries==[] and quoted==[]
    assert repository.calls==1


def test_actual_1517_krx_snapshot_is_not_a_1518_signal():
    # READ ONLY production signal snapshots: both intents used this same pair.
    previous={3:269916.6666666667,5:269800.,10:269800.,20:270012.5,30:270208.3333333333,50:270000.}
    current={3:270000.,5:269850.,10:269775.,20:270000.,30:270200.,50:270035.}
    point=PreparedMaPoint(AT,current,previous,270000.,None,'REST_1MIN_LEGACY')
    for strategy in (1981,2221):
        event=MinuteMaSignalEngine().evaluate_prepared(path=path(strategy),points=(point,))[0]
        assert event.signal_type is SignalType.ENTRY
        assert event.source_bar_time==AT
        assert event.confirmed_at==datetime(2026,9,30,15,18,1)


def test_recorded_integrated_tail_has_no_eligible_1517_crossover():
    # Actual READ ONLY v1_source_bars tail: all ineligible; no 15:17 row.
    recorded=[(14,50,270500.),(14,51,271000.),(14,54,271000.),(14,55,270250.),
        (14,56,270500.),(15,0,270250.),(15,2,270000.),(15,4,270000.),
        (15,5,270250.),(15,6,270000.),(15,8,269500.),(15,11,270000.),
        (15,14,270000.),(15,16,269500.)]
    rows=tuple(MinuteBar(datetime(2026,9,30,h,m),p,p,p,p,0,None,False,
        'KIS_H0UNCNT0_INTEGRATED') for h,m,p in recorded)
    runtime,planner,_,quoted=live(rows)
    assert runtime.run_day(trading_date=DAY)=={}
    assert planner.entries==[] and planner.exits==[] and quoted==[]


def test_official_entry_reaches_both_routes_with_underlying_source_and_actual_finalization():
    runtime,planner,repo,quoted=live(bars())
    runtime.run_day(trading_date=DAY)
    assert len(planner.entries)==2
    assert repo.calls==1 and quoted==['0193W0','0193W0']
    for request in planner.entries:
        event=request['event']
        assert event.confirmed_at==AT+timedelta(minutes=1,milliseconds=247)
        assert event.signal_source=='KIS_H0UNCNT0_INTEGRATED'
        assert signal_evidence(event)['source_bar_time']==AT.isoformat()


@pytest.mark.parametrize('snapshot,expected',[
    (None,[RealFilter.BASE]),
    (RealSnapshot(*([Decimal(1)]*5),True),list(RealFilter)),
    (RealSnapshot(Decimal(1),None,Decimal(1),Decimal(1),Decimal(1),True),[RealFilter.BASE,RealFilter.F1]),
    (RealSnapshot(*([Decimal(1)]*5),False),[RealFilter.BASE]),
])
def test_variants_filter_the_same_official_entry(snapshot,expected):
    routes=[replace(route(filter_code=f),route_id=i+1,variant_id=i+1) for i,f in enumerate(RealFilter)]
    runtime,planner,_,_=live(bars(),routes,snapshot)
    runtime.run_day(trading_date=DAY)
    assert [x['route'].filter_code for x in planner.entries]==expected
    assert len({x['event'].signal_event_key for x in planner.entries})==1


@pytest.mark.parametrize('hour,minute,expected',[(14,49,0),(14,50,2),(15,18,2),(15,19,0)])
def test_live_entry_boundaries(hour,minute,expected):
    runtime,planner,_,_=live(bars(end=AT.replace(hour=hour,minute=minute)))
    runtime.run_day(trading_date=DAY)
    assert len(planner.entries)==expected


def test_cutover_bootstrap_does_not_replay_old_entry():
    runtime,planner,_,_=live(bars(),[route(signal_effective_from=AT)])
    runtime.run_day(trading_date=DAY)
    assert planner.entries==[]
    runtime,planner,_,_=live(bars(),[route(cursor=None)])
    runtime.run_day(trading_date=DAY)
    assert planner.entries==[]


def test_closed_route_retains_normal_exit_responsibility():
    rows=bars()
    rows=tuple(replace(b,close_price=200-b.close_price) for b in rows)
    runtime,planner,_,_=live(rows,[route(active=False)])
    runtime.run_day(trading_date=DAY)
    assert planner.entries==[] and len(planner.exits)==1
    assert planner.exits[0]['event'].signal_type is SignalType.EXIT


def test_common_cycle_computes_ma_and_cross_once_for_legacy_and_real():
    class Counting(MinuteMaSignalEngine):
        def __init__(self): self.prepared=0; self.evaluated=0
        def prepare(self, **kw): self.prepared+=1; return super().prepare(**kw)
        def evaluate_prepared(self, **kw): self.evaluated+=1; return super().evaluate_prepared(**kw)
    engine=Counting(); repo=Repository(bars()); cycle=OfficialSignalCycle(repo,engine=engine)
    points=cycle.prepare(path=path(),bars=cycle.v1_source_bars(stock_code='005930',trading_date=DAY))
    legacy=cycle.evaluate_prepared(path=path(),points=points[-1:])
    _,real=cycle.day(path=path(),trading_date=DAY)
    assert legacy==real and (repo.calls,engine.prepared,engine.evaluated)==(1,1,1)


def test_source_guard_rejects_krx_fallback():
    with pytest.raises(ValueError,match='INTEGRATED_SOURCE'):
        OfficialSignalCycle(Repository(bars(source='REST_1MIN_LEGACY'))).day(path=path(),trading_date=DAY)


def test_integrated_rest_warmup_continues_into_realtime_completed_cross():
    rows=bars()
    warmup=tuple(replace(b,source_name='REST_1MIN_PRE_CUTOVER') for b in rows[:-1])
    cycle=OfficialSignalCycle(Repository(warmup+rows[-1:]))
    points,events=cycle.day(path=path(),trading_date=DAY)
    assert points[-2].source_name=='REST_1MIN_PRE_CUTOVER'
    assert points[-1].source_name=='KIS_H0UNCNT0_INTEGRATED'
    assert len(events)==1 and events[0].signal_type is SignalType.ENTRY
    assert events[0].source_bar_time==AT
    assert events[0].confirmed_at==rows[-1].finalized_at
    assert events[0].previous_ma_values[5]==100


def test_forward_paper_uses_official_cross_not_execution_price_cross(monkeypatch):
    import src.minute_ma.real_paper_runtime as module
    r=MinuteMaRealPaperRuntime(Pool())
    r._forward_strategies=lambda: ((1981,path(),BEFORE),)
    r._bars=lambda *a: bars(source='REST_1MIN_LEGACY')+(MinuteBar(AT+timedelta(minutes=1),123,123,123,123),)
    r._real=lambda *a: {}
    observed=[]
    r._open_incremental=lambda *a: observed.append(a) or 1
    r._close_incremental=lambda *a: 0
    monkeypatch.setattr(module,'PostgresMinuteMaRepository',lambda pool: Repository(bars(crossover=False)))
    assert r.process_day(DAY)==(0,0) and observed==[]
    monkeypatch.setattr(module,'PostgresMinuteMaRepository',lambda pool: Repository(bars()))
    assert r.process_day(DAY)==(1,0)
    assert observed[0][0].minute_path_id==1981
    assert observed[0][1].minute_path_id==7921
    assert observed[0][2].open_price==123


def test_no_new_stop_eod_or_historical_rewrite():
    source=Path('src/minute_ma/real_live.py').read_text(encoding='utf-8')
    assert 'raw_stock_minute' not in source and 'MinuteMaSignalEngine' not in source
    assert 'stop_triggered' not in source and 'EOD_1519' not in source
    migration=Path('database/migrations/20260930_minute_ma_real_official_signal_link.sql').read_text(encoding='utf-8')
    assert 'UPDATE minute_ma_real_variant' in migration
    for table in ('minute_ma_real_live_route','minute_ma_live_intent','live_broker_order',
                  'minute_ma_live_trade','minute_ma_real_capital_epoch','minute_ma_real_paper_trade'):
        assert f'UPDATE {table}' not in migration and f'DELETE FROM {table}' not in migration
