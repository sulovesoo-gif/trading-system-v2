from dataclasses import replace

from src.first_rise_breakout.j_runtime import JMarketRuntime
from src.first_rise_breakout.j_signal import JState
from test.test_first_rise_j import CONFIG, state, at, bar


class Repository:
    def __init__(self):self.saved={};self.events=[]
    def load_or_create(self,candidate):
        return self.saved.setdefault(candidate.candidate_event_id,(JState(candidate),0))
    def save(self,step,*,expected_revision):
        cid=step.state.tracking.candidate_event_id
        assert self.saved[cid][1]==expected_revision
        self.saved[cid]=(step.state,expected_revision+1)
        self.events.append(step)
        return expected_revision+1


class Source:
    previous=1000
    def __init__(self):self.bars=[];self.discarded=[];self.fail=set()
    def completed_bars_from_open(self,*,stock_code,as_of):
        if stock_code in self.fail:raise RuntimeError('REST fixture')
        return [b for b in self.bars if b.bar_time<as_of.replace(second=0,microsecond=0)]
    def previous_close(self,**kwargs):return self.previous
    def discard(self,**kwargs):self.discarded.append(kwargs)


def test_runtime_first_stop_second_and_restart_idempotency():
    repo=Repository();source=Source();runtime=JMarketRuntime(repository=repo,minute_source=source)
    candidate=state().tracking
    runtime.register(candidate,discovered_at=at(9,1))
    source.bars=[bar(9,0,1030),bar(9,1,1025,1000),bar(9,9,1031,1025)]
    runtime.refresh(at=at(9,10),config=CONFIG)
    assert [s.state.sequence for s in repo.events if s.market_entry]==[1]
    source.bars+=[bar(9,10,1032,1020),bar(9,50,1040),bar(9,51,1035,1010),bar(10,0,1041,1035)]
    runtime.refresh(at=at(10,1),config=CONFIG)
    assert [s.state.sequence for s in repo.events if s.market_entry]==[1,2]
    assert [s.market_exit.reason for s in repo.events if s.market_exit]==['STOP_ENTRY_BREAK']
    restarted=JMarketRuntime(repository=repo,minute_source=source)
    restarted.register(candidate,discovered_at=at(9,1))
    count=len(repo.events)
    restarted.refresh(at=at(10,1),config=CONFIG)
    assert len(repo.events)==count
    source.bars+=[bar(10,1,1042,1020),bar(10,20,1050)]
    restarted.refresh(at=at(10,21),config=CONFIG)
    assert len([s for s in repo.events if s.market_entry])==2


def test_invalid_config_previous_and_per_stock_failure_do_not_block_open_exit():
    repo=Repository();source=Source();runtime=JMarketRuntime(repository=repo,minute_source=source)
    a=state().tracking;b=replace(state().tracking,stock_code='OTHER')
    runtime.register(a,discovered_at=at(9,1));runtime.register(b,discovered_at=at(9,1))
    source.bars=[bar(9,0,1030),bar(9,1,1025,1000),bar(9,9,1031,1025)]
    runtime.refresh(at=at(9,10),config=CONFIG)
    source.fail.add('123456');source.previous=None
    source.bars+=[bar(9,10,1032,1020)]
    runtime.refresh(at=at(9,11),config=None)
    assert runtime.states['OTHER'][0].prior_exit_reason=='STOP_ENTRY_BREAK'
    assert runtime.states['123456'][0].open_signal is not None


def test_condition_poll_dispatches_to_j_not_legacy_paper():
    from types import SimpleNamespace
    from src.first_rise_breakout.runtime import FirstRiseBreakoutRuntime
    from src.first_rise_breakout.strategy import FirstRiseBreakoutStrategy
    candidate=state().tracking;jrepo=Repository();source=Source()
    source.bars=[bar(9,0,1030),bar(9,1,1025,1000),bar(9,9,1031,1025)]
    class Repo:
        created=False
        def j_market_repository(self):return jrepo
        def record_condition_hits(self,**kwargs):pass
        def record_candidate(self,**kwargs):
            first=not self.created;self.created=True
            return candidate,first
        def apply(self,*args,**kwargs):raise AssertionError('legacy PAPER must not receive J events')
    search=SimpleNamespace(resolve_seq=lambda _: 'dynamic',candidates=lambda _: [SimpleNamespace(
        stock_code=candidate.stock_code,stock_name='fixture',raw_payload={})])
    runtime=FirstRiseBreakoutRuntime(repository=Repo(),strategy=FirstRiseBreakoutStrategy(),
        condition_search=search,minute_source=source,config=CONFIG,now_provider=lambda:at(9,1))
    runtime.scan_once(at=at(9,1));runtime.refresh_completed_bars(at=at(9,10))
    assert len([s for s in jrepo.events if s.market_entry])==1
    runtime.scan_once(at=at(9,10));runtime.refresh_completed_bars(at=at(9,10))
    assert len([s for s in jrepo.events if s.market_entry])==1
    assert not runtime._states
