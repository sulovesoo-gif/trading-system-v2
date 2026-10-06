from dataclasses import replace
from datetime import datetime, time, timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest

from src.first_rise_breakout.config import FirstRiseRuntimeConfig
from src.first_rise_breakout.j_capital import slot_tier, size_buy
from src.first_rise_breakout.j_signal import JSignalEngine, JState, entry_sequence
from src.first_rise_breakout.models import CandidateState, MinuteBar, ResearchState

CONFIG_ROW = ('Y','09:01','15:00','09:01','15:00','10000000','10000000','100000000')
CONFIG = FirstRiseRuntimeConfig.from_row(CONFIG_ROW)
DAY = datetime(2026, 10, 2)


def at(hour, minute):
    return DAY.replace(hour=hour, minute=minute)


def state():
    return JState(CandidateState(uuid4(), DAY.date(), '123456', ResearchState.DISCOVERED))


def bar(hour, minute, high, low=None, opening=None):
    return MinuteBar(at(hour,minute),D(opening or high),D(high),D(low or high),D(high))


@pytest.mark.parametrize('reference,slot', [(0,10),(19,10),(20,20),(30,30),(100,100),(150,100)])
def test_tiers(reference, slot):
    assert slot_tier(CONFIG,D(reference-10)*1000000).common_slot_amount == slot*1000000


def test_tier_down_and_partial_last_slot():
    assert slot_tier(CONFIG,D('20000000')).common_slot_amount == 30000000
    assert slot_tier(CONFIG,D('9000000')).common_slot_amount == 10000000
    cash=D('25000000')
    quantities=[]
    for _ in range(3):
        result=size_buy(config=CONFIG,realized_net_pnl=0,broker_cash=cash,price=10000,buy_fee_rate=0)
        quantities.append(result.quantity)
        cash-=result.estimated_cash_used
    assert quantities == [1000,1000,500]


@pytest.mark.parametrize('cash,qty',[(7000000,700),(0,0),(9999,0)])
def test_broker_cash_only(cash,qty):
    assert size_buy(config=CONFIG,realized_net_pnl=0,broker_cash=cash,price=10000,buy_fee_rate=0).quantity == qty


def test_fee_and_no_backtest_slippage():
    result=size_buy(config=CONFIG,realized_net_pnl=0,broker_cash=10000,price=10000,buy_fee_rate=D('.000146527'))
    assert result.quantity == 0


@pytest.mark.parametrize('hour,minute,expected',[(9,0,None),(9,1,1),(9,59,1),(10,0,1),(10,1,None),(14,59,None),(15,0,None)])
def test_first_boundary(hour,minute,expected):
    assert entry_sequence(state(),at(hour,minute),start=time(9,1),cutoff=time(15)) == expected


@pytest.mark.parametrize('reason',['STOP_ENTRY_BREAK','BOOK_TENKAN_PROFIT','SESSION_CLOSE'])
@pytest.mark.parametrize('hour,minute',[(9,59),(10,0),(10,1),(14,59),(15,0)])
def test_second_boundary(reason,hour,minute):
    s=replace(state(),sequence=1,prior_exit_reason=reason,prior_exit_time=at(9,55))
    expected=2 if reason=='STOP_ENTRY_BREAK' and time(10)<=time(hour,minute)<time(15) else None
    assert entry_sequence(s,at(hour,minute),start=time(9,1),cutoff=time(15)) == expected


def test_third_and_same_exit_bar_forbidden():
    s=replace(state(),sequence=1,prior_exit_reason='STOP_ENTRY_BREAK',prior_exit_time=at(10,0))
    assert entry_sequence(s,at(10,0),start=time(9,1),cutoff=time(15)) is None
    assert entry_sequence(replace(s,sequence=2),at(10,1),start=time(9,1),cutoff=time(15)) is None
    assert entry_sequence(replace(s,open_signal=s.tracking),at(10,1),start=time(9,1),cutoff=time(15)) is None


def test_market_stop_is_independent_of_actual_fill():
    engine=JSignalEngine(CONFIG); s=state(); bars=[]
    events=[bar(9,0,'1030'),bar(9,1,'1025','1000'),bar(9,9,'1031','1025')]
    entry=None
    for b in events:
        bars.append(b); step=engine.advance(s,b,previous_close=D('1000'),completed_bars=bars)
        s=step.state; entry=step.market_entry or entry
    assert entry is not None
    assert entry.raw_execution_price == D('1031')
    # No paper/broker fill is supplied anywhere, yet independent STOP is tracked.
    b=bar(9,10,'1032','1020','1029');bars.append(b)
    step=engine.advance(s,b,previous_close=D('1000'),completed_bars=bars)
    assert step.market_exit.reason == 'STOP_ENTRY_BREAK'
    assert step.market_exit.raw_execution_price == D('1029')
    assert entry_sequence(step.state,at(10,0),start=time(9,1),cutoff=time(15)) == 2


def test_bootstrap_restores_market_entry_but_never_authorizes_retro_fills():
    engine=JSignalEngine(CONFIG); s=state(); bars=[]
    for b in [bar(9,0,'1030'),bar(9,1,'1025','1000'),bar(9,9,'1031','1025')]:
        bars.append(b);step=engine.advance(s,b,previous_close=D('1000'),completed_bars=bars,bootstrap=True)
        s=step.state
    assert s.sequence==1
    assert step.market_entry.evidence['sequence_replay_only'] is True
    assert step.market_entry.evidence['live_entry_eligible'] is False
    assert step.market_entry.evidence['paper_entry_eligible'] is False


@pytest.mark.parametrize('amount',['0','-1','1.1','NaN','Infinity','1e7','',None])
def test_invalid_money_fails_closed(amount):
    row=list(CONFIG_ROW);row[5]=amount
    with pytest.raises(ValueError):FirstRiseRuntimeConfig.from_row(row)


def test_no_missing_amount_fallback():
    with pytest.raises(ValueError):FirstRiseRuntimeConfig.from_row(CONFIG_ROW[:5])
    with pytest.raises(ValueError):FirstRiseRuntimeConfig.from_row((*CONFIG_ROW[:7],'1'))


def test_first_stop_second_stop_never_third_and_restart_roundtrip():
    from src.first_rise_breakout.j_repository import encode_state, decode_state
    engine=JSignalEngine(CONFIG); s=state(); bars=[]; entries=[]; exits=[]
    events=[bar(9,0,'1030'),bar(9,1,'1025','1000'),bar(9,9,'1031','1025'),
            bar(9,10,'1032','1020','1029'),bar(9,11,'1025','1000'),
            bar(10,0,'1033','1025'),bar(10,1,'1034','1020','1030'),
            bar(10,2,'1026','1020'),bar(10,12,'1035','1026')]
    for b in events:
        bars.append(b)
        step=engine.advance(s,b,previous_close=D('1000'),completed_bars=bars)
        s=decode_state(encode_state(step.state))
        if step.market_entry: entries.append(step.market_entry)
        if step.market_exit: exits.append(step.market_exit)
    assert [e.evidence['signal_sequence'] for e in entries] == [1,2]
    assert entries[1].signal_time == at(10,0)
    assert [e.reason for e in exits] == ['STOP_ENTRY_BREAK','STOP_ENTRY_BREAK']
    assert s.sequence == 2 and s.open_signal is None


def test_0900_completed_peak_not_an_entry():
    engine=JSignalEngine(CONFIG)
    b=bar(9,0,'1030')
    step=engine.advance(state(),b,previous_close=D('1000'),completed_bars=[b])
    assert step.market_entry is None
    assert step.state.tracking.peak_time == at(9,0)
    assert step.state.tracking.peak_price == D('1030')
