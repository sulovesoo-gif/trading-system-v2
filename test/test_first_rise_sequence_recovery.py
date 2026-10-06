from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock
import os
import tempfile
from datetime import datetime,timezone

import pytest

from src.first_rise_breakout.j_repository import encode_state,decode_state
from src.first_rise_breakout.j_runtime import JMarketRuntime
from src.first_rise_breakout.j_signal import JSignalEngine,entry_sequence
from test.test_first_rise_j import CONFIG,state,at,bar
from test.test_first_rise_j_runtime import Repository,Source
from test.test_first_rise_j_wiring_e2e import WiringE2E


def first_stop():
    return [bar(9,0,1030),bar(9,1,1025,1000),bar(9,9,1031,1025),
            bar(9,10,1032,1020),bar(9,50,1040),bar(9,51,1035,1010)]


def recovered(bars):
    repo=Repository();source=Source();source.bars=bars
    runtime=JMarketRuntime(repository=repo,minute_source=source)
    candidate=state().tracking
    runtime.register(candidate,discovered_at=at(10,30))
    runtime.refresh(at=at(10,30),config=CONFIG)
    return runtime,repo,source,candidate


def test_first_stop_recovered_then_fresh_second_and_repeat_poll():
    runtime,repo,source,candidate=recovered(first_stop())
    s=runtime.states[candidate.stock_code][0]
    assert s.sequence==1 and s.prior_exit_reason=='STOP_ENTRY_BREAK'
    runtime.register(candidate,discovered_at=at(10,51))
    source.bars.append(bar(10,50,1041,1035))
    runtime.refresh(at=at(10,51),config=CONFIG)
    entries=[s.market_entry for s in repo.events if s.market_entry]
    assert [e.evidence['signal_sequence'] for e in entries]==[1,2]
    assert entries[0].evidence['sequence_replay_only']
    assert not entries[1].evidence['sequence_replay_only']


def test_past_second_consumed_no_third_restart_duplicate_zero():
    bars=first_stop()+[bar(10,20,1041,1035),bar(10,21,1042,1020)]
    runtime,repo,source,candidate=recovered(bars)
    snapshot=decode_state(encode_state(runtime.states[candidate.stock_code][0]))
    assert snapshot.sequence==2 and snapshot.open_signal is None
    before=len(repo.events)
    repo.roster=lambda **kw:[(snapshot,repo.saved[candidate.candidate_event_id][1],at(10,30))]
    restarted=JMarketRuntime(repository=repo,minute_source=source)
    restarted.restore(at=at(10,40))
    source.bars.extend([bar(10,40,1040,1010),bar(10,50,1043,1035)])
    restarted.refresh(at=at(10,51),config=CONFIG)
    assert len(repo.events)==before


def test_old_price_only_snapshot_is_rebuilt():
    repo=Repository();source=Source();source.bars=first_stop()
    candidate=state().tracking
    old=replace(state(),tracking=replace(candidate,last_observed_at=at(10,0)))
    repo.saved[candidate.candidate_event_id]=(old,8)
    repo.roster=lambda **kw:[(old,8,at(10,0))]
    runtime=JMarketRuntime(repository=repo,minute_source=source)
    runtime.restore(at=at(10,30));runtime.refresh(at=at(10,30),config=CONFIG)
    assert runtime.states[candidate.stock_code][0].prior_exit_reason=='STOP_ENTRY_BREAK'
    assert len([e for e in repo.events if e.market_entry])==1


@pytest.mark.parametrize('reason',['BOOK_TENKAN_PROFIT','SESSION_CLOSE'])
def test_restored_non_stop_never_second(reason):
    s=replace(state(),sequence=1,prior_exit_reason=reason,prior_exit_time=at(9,55))
    s=decode_state(encode_state(s))
    assert entry_sequence(s,at(10,50),start=CONFIG.live_entry_start,cutoff=CONFIG.live_entry_cutoff) is None


def test_unit_uses_existing_nonprivileged_collector_account():
    root=Path(__file__).resolve().parents[1]
    live=(root/'systemd/trading-first-rise-live.service').read_text()
    assert 'User=ubuntu\n' in live
    assert 'WorkingDirectory=/home/ubuntu/projects/trading-system-v2\n' in live
    assert 'ExecStart=/home/ubuntu/projects/trading-system-v2/venv/bin/python scripts/runtime/run_first_rise_live.py' in live


def test_two_service_cache_instances_share_private_cache():
    from src.collector.raw.token_cache import TokenCache
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'token.json'
        collector,live=TokenCache(path),TokenCache(path)
        for writer,reader in ((collector,live),(live,collector),(collector,live)):
            with writer.refresh_lock():
                writer.save(access_token='TEST_ONLY',expires_at=datetime.now(timezone.utc)+timedelta(hours=1))
            assert reader.load()[0]=='TEST_ONLY'
            if os.name=='posix':
                assert path.stat().st_uid==os.getuid()
                assert path.stat().st_mode & 0o777==0o600


class SequenceRecoveryPostgresTests(WiringE2E):
    def replay_setup(self,bars):
        # pg_temp-only fixture reset; never connected to production tables.
        self.assertEqual(self.query('SHOW search_path'),[('pg_temp',)])
        self.c.execute('TRUNCATE first_rise_j_candidate, first_rise_j_market_signal CASCADE')
        self.c.commit()
        self.bars=bars;self.now=at(10,30)
        self.market=JMarketRuntime(repository=self.market.repository,minute_source=self.source)
        self.market.register(self.candidate,discovered_at=self.now)
        self.market.refresh(at=self.now,config=self.config);self.c.commit()

    def test_replayed_open_second_cannot_plan_or_submit_even_after_activation(self):
        self.replay_setup(first_stop()+[bar(10,20,1041,1035)])
        self.assertEqual(self.query('SELECT signal_sequence FROM first_rise_j_market_signal ORDER BY 1'),[(1,),(2,)])
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_paper_trade'),[(0,)])
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_shadow_trade'),[(0,)])
        self.cycle()
        for (signal,) in self.query('SELECT market_signal_id FROM first_rise_j_market_signal'):
            self.assertEqual(self.live.planner.plan_buy(signal,context=self.context,at=self.now,
                price_lookup=Mock(side_effect=AssertionError()),cash_lookup=Mock()),'NO_REPLAY')
        self.assertEqual(self.query('SELECT count(*) FROM live_order_request'),[(0,)])
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_live_intent'),[(0,)])
        self.assertEqual(self.posts,[])
        self.market.restore(at=self.now+timedelta(minutes=1))
        self.market.refresh(at=self.now+timedelta(minutes=1),config=self.config);self.c.commit()
        self.assertEqual(self.query('SELECT count(*) FROM first_rise_j_market_signal'),[(2,)])

    def test_historical_first_stop_fresh_second_uses_market_not_actual(self):
        self.replay_setup(first_stop())
        self.cycle();self.assertEqual(self.posts,[])
        self.bars.append(bar(10,50,1041,1035));self.now=at(10,51)
        self.market.refresh(at=self.now,config=self.config);self.c.commit()
        self.assertEqual(len(self.live.planner.entry_signals(at=self.now)),1)
        self.cycle()
        self.assertEqual(len(self.posts),1)  # Fake broker: fresh SECOND only.
        self.assertEqual(self.query('SELECT count(*) FROM live_order_request'),[(1,)])
