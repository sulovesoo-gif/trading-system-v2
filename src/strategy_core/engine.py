"""Canonical completed-bar Strategy Core for S1, S2, and S3.

The functions return price-free :class:`SignalDecision` objects.  They accept
only completed bars and definition parameters, so research replay, historical
validation, and a future LIVE adapter share exactly the same public API.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Iterable, Sequence

from .bars import CompletedBar
from .contracts import DecisionType, SignalDecision, decision_id
from .registry import StrategyDefinition
from .strategies import _decision


OR_START = time(9, 0)
OR_END = time(9, 30)


def _body_ratio(bar: CompletedBar) -> float:
    span = bar.high - bar.low
    return abs(bar.close - bar.open) / span if span else 0.0


def _next_actual_bar(bars: Sequence[CompletedBar], at: datetime) -> datetime | None:
    return next((bar.time for bar in bars if bar.time > at), None)


def _regular_day(bars: Iterable[CompletedBar]) -> tuple[CompletedBar, ...]:
    return tuple(sorted((bar for bar in bars if OR_START <= bar.time.time() <= time(15, 19)), key=lambda value: value.time))


def generate_s1(definition: StrategyDefinition, source_bars: Iterable[CompletedBar]) -> tuple[SignalDecision, ...]:
    """Generate the single approved S1 entry from a source-day completed-bar set."""
    bars = _regular_day(source_bars)
    opening = [bar for bar in bars if OR_START <= bar.time.time() < OR_END]
    if not opening:
        return ()
    or_high, or_low = max(bar.high for bar in opening), min(bar.low for bar in opening)
    breakout = next((bar for index, bar in enumerate(bars) if bar.time.time() > OR_END and bar.close > or_high
                     and index > 0 and bars[index - 1].close <= or_high and bar.close > bar.open
                     and _body_ratio(bar) >= 0.50), None)
    if breakout is None:
        return ()
    pullback = next((bar for bar in bars if breakout.time < bar.time <= breakout.time + timedelta(minutes=30)
                     and bar.low <= or_high * 1.003 and bar.close >= or_high * 0.997
                     and bar.volume <= breakout.volume), None)
    if pullback is None:
        return ()
    restart = next((bar for index, bar in enumerate(bars) if pullback.time < bar.time <= pullback.time + timedelta(minutes=20)
                    and index > 0 and bar.close > bar.open and bar.close > bars[index - 1].high
                    and bar.close > or_high and _body_ratio(bar) >= 0.50
                    and bar.volume >= pullback.volume * 1.10), None)
    if restart is None:
        return ()
    target = _next_actual_bar(bars, restart.time)
    if target is None:
        return ()
    return (_decision(
        definition, DecisionType.ENTRY, restart.time, entry_reason="S1_OR_PULLBACK_RESTART", target_time=target,
        reference_levels={"or_high": or_high, "or_low": or_low, "pullback_low": pullback.low},
        invalidation_levels={"pullback_low": pullback.low}, required_lookback=30,
        evidence={"breakout_time": breakout.time.isoformat(), "pullback_time": pullback.time.isoformat(), "restart": True},
    ),)


def generate_s2(definition: StrategyDefinition, source_bars: Iterable[CompletedBar]) -> tuple[SignalDecision, ...]:
    """Generate the single approved S2 entry using cumulative same-day VWAP."""
    bars = _regular_day(source_bars)
    opening = [bar for bar in bars if OR_START <= bar.time.time() < OR_END]
    if not opening:
        return ()
    or_high, or_low = max(bar.high for bar in opening), min(bar.low for bar in opening)
    breakout = next((bar for bar in bars if bar.time.time() > OR_END and bar.high > or_high and bar.close >= or_high), None)
    if breakout is None:
        return ()
    cumulative_value = 0.0
    cumulative_volume = 0.0
    vwap_by_time: dict[datetime, float] = {}
    for bar in bars:
        cumulative_value += ((bar.high + bar.low + bar.close) / 3.0) * bar.volume
        cumulative_volume += bar.volume
        vwap_by_time[bar.time] = cumulative_value / cumulative_volume if cumulative_volume else 0.0
    failed = next((bar for bar in bars if breakout.time < bar.time <= breakout.time + timedelta(minutes=20)
                   and bar.close < or_high and bar.close < bar.open and bar.close > vwap_by_time[bar.time]), None)
    if failed is None:
        return ()
    target = _next_actual_bar(bars, failed.time)
    if target is None:
        return ()
    return (_decision(
        definition, DecisionType.ENTRY, failed.time, entry_reason="S2_FAILED_OR_VWAP", target_time=target,
        reference_levels={"or_high": or_high, "or_low": or_low, "vwap": vwap_by_time[failed.time]},
        required_lookback=30, evidence={"breakout_time": breakout.time.isoformat(), "failed_breakout": True},
    ),)


def s1_exit(definition: StrategyDefinition, entry: SignalDecision, source_bars: Iterable[CompletedBar]) -> SignalDecision:
    """Emit only S1 exit timing/reason; historical adapter selects execution price."""
    bars = _regular_day(source_bars)
    pullback_low = float(entry.invalidation_levels["pullback_low"])
    assert entry.target_time is not None
    trigger = next((bar for bar in bars if entry.target_time <= bar.time <= entry.target_time + timedelta(minutes=30)
                    and bar.time.time() <= time(15, 18) and bar.close < pullback_low), None)
    if trigger is not None:
        target = _next_actual_bar(bars, trigger.time)
        if target is None:
            return _decision(definition, DecisionType.NONE, trigger.time)
        return _decision(definition, DecisionType.EXIT, trigger.time, exit_reason="PULLBACK_LOW_BREAK_WITHIN30",
                         target_time=target, invalidation_levels={"pullback_low": pullback_low})
    eod = next((bar for bar in bars if bar.time.time() == time(15, 19)), None)
    if eod is None:
        return _decision(definition, DecisionType.NONE, entry.signal_time)
    return _decision(definition, DecisionType.EXIT, eod.time, exit_reason="EOD_1519", target_time=eod.time,
                     invalidation_levels={"pullback_low": pullback_low})


def fixed30_exit(definition: StrategyDefinition, entry: SignalDecision) -> SignalDecision:
    assert entry.target_time is not None
    target = entry.target_time + timedelta(minutes=30)
    return _decision(definition, DecisionType.EXIT, target, exit_reason="FIXED_30", target_time=target,
                     evidence={"hold_minutes": 30})


def generate_s3_shared(definition: StrategyDefinition, source_bars: Iterable[CompletedBar]) -> tuple[SignalDecision, ...]:
    """Generate exactly one S3 shared entry; variants are applied only at exit."""
    bars = _regular_day(source_bars)
    climax: CompletedBar | None = None
    for index, bar in enumerate(bars):
        if climax is None:
            if not (time(9, 10) <= bar.time.time() <= time(14, 50)) or index < 20:
                continue
            ret5 = (bar.close / bars[index - 5].close - 1.0) if bars[index - 5].close else 0.0
            previous = bars[index - 20:index]
            rvol20 = bar.volume / (sum(value.volume for value in previous) / len(previous)) if previous and sum(value.volume for value in previous) else 0.0
            wick = bar.high - max(bar.open, bar.close)
            body = abs(bar.close - bar.open)
            if ret5 >= float(definition.entry_params.get("move_threshold", .008)) and rvol20 >= float(definition.entry_params.get("rvol_threshold", 2.0)) and body > 0 and wick / body >= .5:
                climax = bar
            continue
        if bar.time > climax.time + timedelta(minutes=8):
            return ()
        if bar.high <= climax.high and bar.close < min(climax.open, climax.close) and bar.close < bar.open:
            target = _next_actual_bar(bars, bar.time)
            if target is None:
                return ()
            shared = decision_id(strategy_code="S3_VOLUME_CLIMAX_REVERSAL", signal_time=bar.time, decision_type=DecisionType.ENTRY, scope="shared")
            return (_decision(
                definition, DecisionType.ENTRY, bar.time, entry_reason="S3_VOLUME_CLIMAX_REVERSAL", target_time=target,
                reference_levels={"climax_high": climax.high, "climax_time": climax.time.isoformat()}, required_lookback=20,
                evidence={"confirmation_within_minutes": 8, "entry_shared": True}, shared_entry=shared,
            ),)
    return ()


def s3_exit(definition: StrategyDefinition, entry: SignalDecision, source_bars: Iterable[CompletedBar], execution_bars: Iterable[CompletedBar], *, structure_bars: int) -> SignalDecision:
    """Apply clock-minute structure, execution-product stop, and MAX30 arbitration."""
    source = _regular_day(source_bars)
    execution = tuple(sorted(execution_bars, key=lambda value: value.time))
    assert entry.target_time is not None
    entry_execution = next((bar for bar in execution if bar.time == entry.target_time), None)
    if entry_execution is None:
        return _decision(definition, DecisionType.NONE, entry.signal_time)
    structure_trigger = next((bar for bar in source if bar.time >= entry.target_time + timedelta(minutes=5)
                              and bar.close > max((prior.high for prior in source if bar.time - timedelta(minutes=structure_bars) <= prior.time < bar.time), default=float("inf"))), None)
    structure_target = _next_actual_bar(execution, structure_trigger.time) if structure_trigger else None
    stop_trigger = next((bar for bar in execution if bar.time >= entry.target_time and bar.close <= entry_execution.open * .975), None)
    stop_target = _next_actual_bar(execution, stop_trigger.time) if stop_trigger else None
    max_target = entry.target_time + timedelta(minutes=30)
    candidates = [(max_target, 2, "MAX_30", max_target)]
    if structure_target is not None:
        candidates.append((structure_target, 0, "STRUCTURE_RECLAIM", structure_trigger.time))
    if stop_target is not None:
        candidates.append((stop_target, 1, "STOP_2.5", stop_trigger.time))
    target, _priority, reason, trigger = min(candidates, key=lambda value: (value[0], value[1]))
    return _decision(definition, DecisionType.EXIT, trigger, exit_reason=reason, target_time=target,
                     reference_levels={"entry_price": entry_execution.open}, required_lookback=structure_bars,
                     evidence={"structure_window_minutes": structure_bars, "stop_reference": "0197X0_COMPLETED_CLOSE"})
