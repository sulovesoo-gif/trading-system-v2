"""Offline V1.9 market replay. No API, database write, or execution consumer.

Replay only what had been observed by each recorded refresh. The discovery
minute is never bootstrap; it becomes eligible only once actually complete.
"""
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from itertools import groupby

from .models import MinuteBar
from .j_signal import JSignalEngine, JState, JStep


def replay_market(*, candidate, discovered_at, rows, config, initial_state=None, historical_v13=True):
    engine=JSignalEngine(config,historical_v13=historical_v13)
    state=initial_state or JState(candidate)
    cutoff=discovered_at.replace(second=0,microsecond=0)
    seen={};previous=None;invalid=False;steps=[]
    ordered=sorted(rows,key=lambda r:(r['observed_as_of'],r['bar_time']))
    for observed,batch in groupby(ordered,key=lambda r:r['observed_as_of']):
        for row in batch:
            if row['bar_time'].date()!=candidate.business_date or row['bar_time']>=observed.replace(second=0,microsecond=0):
                raise ValueError('FIRST_RISE_REPLAY_INCOMPLETE_OR_WRONG_DAY')
            if row.get('stock_code',candidate.stock_code)!=candidate.stock_code:
                raise ValueError('FIRST_RISE_REPLAY_STOCK_MISMATCH')
            seen.setdefault(row['bar_time'],row)
            if not historical_v13:continue
            try:
                value=Decimal(str(row.get('previous_close_price')))
                if not value.is_finite() or value<=0 or (previous is not None and value!=previous):
                    raise ValueError('invalid previous close')
                previous=value
            except (ValueError,InvalidOperation):invalid=True
        bars=[MinuteBar.from_mapping(seen[t],source='KIS_FHKST03010200') for t in sorted(seen)]
        if not historical_v13 or (not invalid and previous is not None):
            for bar in bars:
                step=engine.advance(state,bar,previous_close=previous,completed_bars=bars,
                                    bootstrap=bar.bar_time<cutoff)
                if step.state!=state or step.market_entry or step.market_exit:
                    steps.append(step)
                state=step.state
        elif state.open_signal is not None:
            decision=engine.formula.exit_from_completed_bars(state.open_signal,bars)
            if decision.create_exit:
                state=replace(state,open_signal=None,prior_exit_reason=decision.reason,
                              prior_exit_time=decision.after.last_observed_at)
                steps.append(JStep(state,market_exit=decision))
        step=engine.session_close(state,bars,at=observed)
        if step.market_exit:steps.append(step)
        state=step.state
    return state,steps
