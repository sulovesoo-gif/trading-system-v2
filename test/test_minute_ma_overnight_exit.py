from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from src.minute_ma.overnight_exit import OvernightPolicy, OvernightEvent, UP, DOWNFLAT, evidence_for
from src.minute_ma.engine import SignalEvent, SignalType
from src.minute_ma.contracts import MinuteBar
from test.test_minute_ma_real_official_signals import path

DAY = date(2026, 10, 1)
def at(minute): return datetime(2026, 10, 1, 9, minute)

class Pool:
    def __init__(self, up=True, down=True, close=100):
        self.switches = (up, down)
        self.close = close
        self.calls = []
    @contextmanager
    def connection(self): yield self
    @contextmanager
    def cursor(self): yield self
    def execute(self, sql, params): self.calls.append((sql, params))
    def fetchone(self):
        sql, args = self.calls[-1]
        if 'common_code' in sql:
            return ('Y' if self.switches[0 if '_UP_' in args[0] else 1] else 'N',)
        return (date(2026, 9, 30), self.close)

def bars(price):
    return (MinuteBar(at(0), price, price, price, price, source_name='KIS_H0UNCNT0_INTEGRATED'),)

@pytest.mark.parametrize('up,down',[(True,True),(True,False),(False,True),(False,False)])
@pytest.mark.parametrize('price,reason,minute',[(101,UP,3),(100,DOWNFLAT,10),(99,DOWNFLAT,10)])
def test_four_independent_switch_combinations(up,down,price,reason,minute):
    policy=OvernightPolicy(Pool(up,down))
    event=policy.event(path(),DAY,bars(price),(),at(11))
    enabled=up if reason==UP else down
    assert bool(event)==enabled
    if event:
        assert event.signal_source==reason and event.source_bar_time==at(minute)
        assert evidence_for(event)['direction_previous_close']=='100'

@pytest.mark.parametrize('price,deadline',[(101,3),(99,10)])
def test_restart_before_after_deadline_and_stable_key(price,deadline):
    policy=OvernightPolicy(Pool())
    assert policy.event(path(),DAY,bars(price),(),at(deadline-1)) is None
    a=policy.event(path(),DAY,bars(price),(),at(deadline))
    b=OvernightPolicy(Pool()).event(path(),DAY,bars(price),(),at(deadline+1))
    assert a.signal_event_key==b.signal_event_key

@pytest.mark.parametrize('price,minute',[(101,2),(99,7)])
def test_normal_ma_before_deadline_always_wins(price,minute):
    normal=SignalEvent(1,'test',SignalType.EXIT,at(minute),at(minute+1),'normal',True,{}, {})
    assert OvernightPolicy(Pool()).event(path(),DAY,bars(price),(normal,),at(11)) is None

@pytest.mark.parametrize('close,rows',[(None,bars(101)),(100,()),(0,bars(101)),
    (100,(replace(bars(101)[0],signal_eligible=False),))])
def test_missing_price_preserves_normal_lifecycle(close,rows,caplog):
    assert OvernightPolicy(Pool(close=close)).event(path(),DAY,rows,(),at(11)) is None
    assert 'MISSING' in caplog.text

def test_cycle_config_reload_and_cached_direction():
    pool=Pool()
    policy=OvernightPolicy(pool)
    policy.event(path(),DAY,bars(101),(),at(4))
    policy.event(path(),DAY,bars(101),(),at(5))
    assert len(pool.calls)==3  # two uncached switches + one price read per stock/day
    pool.switches=(False,False)
    assert OvernightPolicy(pool).event(path(),DAY,bars(101),(),at(6)) is None

def test_no_entry_price_or_new_ledger_dependency():
    event=OvernightPolicy(Pool()).event(path(),DAY,bars(101),(),at(4))
    assert isinstance(event,OvernightEvent)
    assert 'underlying_entry_reference_price' not in evidence_for(event)
    assert event.signal_source==UP
