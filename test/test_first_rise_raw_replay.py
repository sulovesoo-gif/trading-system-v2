from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.first_rise_breakout.minute_source import SameDayMinutePeakSource
from src.first_rise_breakout.raw_replay import replay_market
from src.first_rise_breakout.j_repository import encode_state, decode_state
from test.test_first_rise_j import CONFIG, state, at


def rows_for_boundary():
    discovered=at(10,14).replace(second=6)
    rows=[]
    for minute,high,low in [(0,1030,1030),(5,1025,1020),(14,1031,1025)]:
        rows.append(dict(stock_code='123456',bar_time=at(10,minute),
            open_price=D(high),high_price=D(high),low_price=D(low),close_price=D(high),
            volume=100,previous_close_price=D(1000),
            observed_as_of=discovered if minute<14 else at(10,15)))
    return discovered,rows


def second_state():
    return replace(state(),sequence=1,prior_exit_reason='STOP_ENTRY_BREAK',prior_exit_time=at(9,55))


def test_discovery_minute_completed_once_and_restart():
    discovered,rows=rows_for_boundary();initial=second_state()
    before,events=replay_market(candidate=initial.tracking,discovered_at=discovered,
        rows=rows[:2],config=CONFIG,initial_state=initial)
    assert not any(e.market_entry for e in events)
    restarted=decode_state(encode_state(before))
    after,events=replay_market(candidate=initial.tracking,discovered_at=discovered,
        rows=rows,config=CONFIG,initial_state=restarted)
    entries=[e.market_entry for e in events if e.market_entry]
    assert len(entries)==1
    assert entries[0].signal_time==at(10,14)
    assert after.sequence==2
    again,events=replay_market(candidate=initial.tracking,discovered_at=discovered,
        rows=rows,config=CONFIG,initial_state=decode_state(encode_state(after)))
    assert not any(e.market_entry for e in events)
    assert again==after


def test_in_progress_bar_rejected_and_late_first_forbidden():
    discovered,rows=rows_for_boundary();initial=state()
    _,events=replay_market(candidate=initial.tracking,discovered_at=discovered,rows=rows,config=CONFIG)
    assert not any(e.market_entry for e in events)
    rows[-1]['observed_as_of']=discovered
    with pytest.raises(ValueError,match='INCOMPLETE'):
        replay_market(candidate=initial.tracking,discovered_at=discovered,rows=rows,config=CONFIG)


def test_bootstrap_signal_never_reused_after_restart():
    discovered,rows=rows_for_boundary();initial=second_state()
    rows[-1]['bar_time']=at(10,13);rows[-1]['observed_as_of']=discovered
    saved,events=replay_market(candidate=initial.tracking,discovered_at=discovered,
        rows=rows,config=CONFIG,initial_state=initial)
    entries=[e.market_entry for e in events if e.market_entry]
    assert len(entries)==1 and saved.sequence==2
    assert entries[0].evidence['sequence_replay_only']
    assert not entries[0].evidence['live_entry_eligible']
    _,events=replay_market(candidate=initial.tracking,discovered_at=discovered,
        rows=rows,config=CONFIG,initial_state=decode_state(encode_state(saved)))
    assert not any(e.market_entry for e in events)


def payload(previous):
    return {'output1':{'stck_prdy_clpr':previous},'output2':[
        {'stck_bsop_date':'20261002','stck_cntg_hour':'101400','stck_oprc':'1030',
         'stck_hgpr':'1031','stck_lwpr':'1025','stck_prpr':'1030','cntg_vol':'100','acml_tr_pbmn':'100000'}]}


@pytest.mark.parametrize('previous',['1000',None,'','0','-1','bad'])
def test_actual_collector_timestamp_previous_and_invalid_exit_bars(previous):
    data=payload(previous);calls=[]
    client=SimpleNamespace(last_payload=data,get=lambda **kw:(calls.append(kw) or data))
    source=SameDayMinutePeakSource(StockMinuteCollector(client))
    bars=source.completed_bars_from_open(stock_code='123456',as_of=at(10,15))
    assert len(bars)==1
    assert bars[0].bar_time.replace(tzinfo=None)==at(10,14)
    assert all(c['tr_id']=='FHKST03010200' for c in calls)
    assert source.previous_close(stock_code='123456',business_date=at(10,14).date())==(D(1000) if previous=='1000' else None)


def test_changed_previous_close_blocks_entry_but_retains_exit_bars():
    _,rows=rows_for_boundary()
    source=SameDayMinutePeakSource(SimpleNamespace(collect=lambda **_:rows))
    source.completed_bars_from_open(stock_code='123456',as_of=at(10,15))
    rows[-1]['previous_close_price']=D(1001)
    assert source.completed_bars_from_open(stock_code='123456',as_of=at(10,16))
    assert source.previous_close(stock_code='123456',business_date=at(10,16).date()) is None
