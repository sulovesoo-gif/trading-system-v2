"""Canonical, side-effect-free reproduction of ``run_strategy_master_backtest``.

This module intentionally preserves the historical procedure's semantics.  It
does not substitute the FROZEN LIVE Champion rules and it does not add a stop
that the historical SQL did not calculate.  Database access, pricing, costs,
and persistence remain outer-adapter responsibilities.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Iterable, Sequence

from src.strategy_core.bars import CompletedBar
from src.strategy_core.contracts import DecisionType, SignalDecision, decision_id
from src.strategy_core.registry import StrategyDefinition


_SESSION_START = time(9, 0)
_SESSION_END = time(15, 19)


def _ordered(bars: Iterable[CompletedBar]) -> tuple[CompletedBar, ...]:
    return tuple(sorted((bar for bar in bars if _SESSION_START <= bar.time.time() <= _SESSION_END), key=lambda bar: bar.time))


def _body_ratio(bar: CompletedBar) -> float:
    span = bar.high - bar.low
    return abs(bar.close - bar.open) / span if span else 0.0


def _wick_ratio(bar: CompletedBar, *, upper: bool) -> float:
    body = abs(bar.close - bar.open)
    if not body:
        return 0.0
    wick = bar.high - max(bar.open, bar.close) if upper else min(bar.open, bar.close) - bar.low
    return wick / body


def _next_actual(bars: Sequence[CompletedBar], at: datetime) -> datetime | None:
    return next((bar.time for bar in bars if bar.time > at), None)


def _last_eod(bars: Sequence[CompletedBar]) -> datetime | None:
    eligible = [bar.time for bar in bars if bar.time.time() <= _SESSION_END]
    return eligible[-1] if eligible else None


def _decision(definition: StrategyDefinition, kind: DecisionType, signal_time: datetime, *, target: datetime | None,
              entry_reason: str | None = None, exit_reason: str | None = None, levels: dict | None = None,
              evidence: dict | None = None) -> SignalDecision:
    scope = definition.strategy_instance or str(definition.strategy_id)
    return SignalDecision(
        decision_id=decision_id(strategy_code=definition.strategy_code, signal_time=signal_time, decision_type=kind, scope=scope),
        strategy_id=definition.strategy_id, strategy_code=definition.strategy_code,
        strategy_version=definition.strategy_version, code_commit=definition.code_commit,
        signal_stock_code=definition.signal_stock_code, signal_direction=definition.signal_direction,
        signal_time=signal_time, execution_stock_code=definition.execution_stock_code,
        execution_direction=definition.execution_direction, decision_type=kind,
        entry_reason=entry_reason, exit_reason=exit_reason, target_time=target,
        reference_levels=levels or {}, parameter_snapshot={"entry": dict(definition.entry_params), "exit": dict(definition.exit_params)},
        data_quality_status="UNVERIFIED", evidence=evidence or {},
    )


class ResearchMasterCore:
    """Evaluate one master row through the four historical family algorithms."""

    def entries(self, definition: StrategyDefinition, source_bars: Iterable[CompletedBar]) -> tuple[SignalDecision, ...]:
        bars = _ordered(source_bars)
        if not bars:
            return ()
        family = definition.strategy_code
        # strategy_group is encoded in current master strategy_code only by the
        # outer adapter; allowing it in entry params makes that adapter explicit.
        family = str(definition.entry_params.get("strategy_group", family))
        if family == "S1_OR_PULLBACK_RESTART":
            entry = self._s1(definition, bars)
        elif family == "S2_FAILED_OR_VWAP":
            entry = self._s2(definition, bars)
        elif family == "S3_VOLUME_CLIMAX_REVERSAL":
            entry = self._s3(definition, bars)
        elif family == "S4_AFTERNOON_MOMENTUM":
            entry = self._s4(definition, bars)
        else:
            raise ValueError(f"unsupported historical strategy family: {family}")
        return (entry,) if entry is not None else ()

    def exit(self, definition: StrategyDefinition, entry: SignalDecision, source_bars: Iterable[CompletedBar],
             execution_bars: Iterable[CompletedBar]) -> SignalDecision | None:
        """Reproduce the procedure's exit timestamp decision, price-free."""
        source, execution = _ordered(source_bars), _ordered(execution_bars)
        if entry.target_time is None:
            return None
        params = dict(definition.exit_params)
        exit_type = params.get("type")
        if exit_type == "FIXED":
            minutes = int(params["hold_minutes"])
            target = entry.target_time + timedelta(minutes=minutes)
            return _decision(definition, DecisionType.EXIT, target, target=target, exit_reason="FIXED")
        if exit_type == "NO_STOP_EOD":
            target = _last_eod(execution)
            return _decision(definition, DecisionType.EXIT, target or entry.signal_time, target=target, exit_reason="EOD_1519") if target else None
        if exit_type == "PULLBACK_LOW_BREAK_EOD":
            low = float(entry.reference_levels["pullback_low"])
            trigger = next((bar for bar in source if bar.time >= entry.target_time and bar.time.time() <= time(15, 18) and bar.close < low), None)
            if trigger:
                target = _next_actual(execution, trigger.time)
                return _decision(definition, DecisionType.EXIT, trigger.time, target=target, exit_reason="PULLBACK_LOW_BREAK", levels={"pullback_low": low}) if target else None
            target = _last_eod(execution)
            return _decision(definition, DecisionType.EXIT, target or entry.signal_time, target=target, exit_reason="EOD_1519", levels={"pullback_low": low}) if target else None
        if exit_type in {"STRUCTURE_MAX30", "STRUCTURE_EOD", "STRUCTURE_MAX30_STOP"}:
            bars_count = int(params["bars"])
            trigger = self._structure_trigger(definition, source, entry.target_time, bars_count)
            if trigger:
                target = _next_actual(execution, trigger.time)
                if target:
                    return _decision(definition, DecisionType.EXIT, trigger.time, target=target, exit_reason="STRUCTURE_OR_TIME", evidence={"clock_window_minutes": bars_count})
            target = entry.target_time + timedelta(minutes=30) if exit_type in {"STRUCTURE_MAX30", "STRUCTURE_MAX30_STOP"} else _last_eod(execution)
            return _decision(definition, DecisionType.EXIT, target or entry.signal_time, target=target, exit_reason="STRUCTURE_OR_TIME", evidence={"clock_window_minutes": bars_count}) if target else None
        target = entry.target_time + timedelta(minutes=30)
        return _decision(definition, DecisionType.EXIT, target, target=target, exit_reason="UNKNOWN")

    def _s1(self, definition: StrategyDefinition, bars: Sequence[CompletedBar]) -> SignalDecision | None:
        minutes = int(definition.entry_params["or_minutes"])
        opening = [bar for bar in bars if _SESSION_START <= bar.time.time() < (datetime.combine(bars[0].time.date(), _SESSION_START) + timedelta(minutes=minutes)).time()]
        if not opening:
            return None
        high, low = max(bar.high for bar in opening), min(bar.low for bar in opening)
        direction = definition.signal_direction
        boundary = (datetime.combine(bars[0].time.date(), _SESSION_START) + timedelta(minutes=minutes)).time()
        prior_close: float | None = None
        breakout: CompletedBar | None = None
        for bar in bars:
            if bar.time.time() > boundary:
                crosses = bar.close > high and (prior_close if prior_close is not None else high) <= high if direction == "LONG" else bar.close < low and (prior_close if prior_close is not None else low) >= low
                candle = bar.close > bar.open if direction == "LONG" else bar.close < bar.open
                if crosses and candle and _body_ratio(bar) >= float(definition.entry_params.get("body_ratio_min", .5)):
                    breakout = bar; break
            prior_close = bar.close
        if not breakout:
            return None
        pullback = next((bar for bar in bars if breakout.time < bar.time <= breakout.time + timedelta(minutes=30)
                         and ((bar.low <= high * 1.003 and bar.close >= high * .997) if direction == "LONG" else (bar.high >= low * .997 and bar.close <= low * 1.003))
                         and bar.volume <= breakout.volume), None)
        if not pullback:
            return None
        restart = next((bar for index, bar in enumerate(bars) if pullback.time < bar.time <= pullback.time + timedelta(minutes=20) and index > 0
                        and ((bar.close > bar.open and bar.close > bars[index - 1].high and bar.close > high) if direction == "LONG" else (bar.close < bar.open and bar.close < bars[index - 1].low and bar.close < low))
                        and _body_ratio(bar) >= float(definition.entry_params.get("body_ratio_min", .5))
                        and bar.volume >= pullback.volume * float(definition.entry_params.get("restart_volume_ratio", 1.1))), None)
        target = _next_actual(bars, restart.time) if restart else None
        return _decision(definition, DecisionType.ENTRY, restart.time, target=target, entry_reason="S1_OR_PULLBACK_RESTART", levels={"or_high": high, "or_low": low, "pullback_low": pullback.low}, evidence={"breakout_time": breakout.time.isoformat(), "pullback_time": pullback.time.isoformat()}) if restart and target else None

    def _s2(self, definition: StrategyDefinition, bars: Sequence[CompletedBar]) -> SignalDecision | None:
        minutes = int(definition.entry_params["or_minutes"])
        boundary = (datetime.combine(bars[0].time.date(), _SESSION_START) + timedelta(minutes=minutes)).time()
        opening = [bar for bar in bars if _SESSION_START <= bar.time.time() < boundary]
        if not opening:
            return None
        high, low = max(bar.high for bar in opening), min(bar.low for bar in opening)
        direction = definition.signal_direction
        breakout = next((bar for bar in bars if bar.time.time() > boundary and ((bar.high > high and bar.close >= high) if direction == "SHORT" else (bar.low < low and bar.close <= low))), None)
        if not breakout:
            return None
        value = volume = 0.0; vwaps: dict[datetime, float] = {}
        for bar in bars:
            value += ((bar.high + bar.low + bar.close) / 3) * bar.volume; volume += bar.volume
            vwaps[bar.time] = value / volume if volume else 0.0
        failed = next((bar for bar in bars if breakout.time < bar.time <= breakout.time + timedelta(minutes=20)
                       and ((bar.close < high and bar.close < bar.open and bar.close > vwaps[bar.time]) if direction == "SHORT" else (bar.close > low and bar.close > bar.open and bar.close < vwaps[bar.time]))), None)
        target = _next_actual(bars, failed.time) if failed else None
        return _decision(definition, DecisionType.ENTRY, failed.time, target=target, entry_reason="S2_FAILED_OR_VWAP", levels={"or_high": high, "or_low": low, "vwap": vwaps[failed.time]}, evidence={"breakout_time": breakout.time.isoformat()}) if failed and target else None

    def _s3(self, definition: StrategyDefinition, bars: Sequence[CompletedBar]) -> SignalDecision | None:
        threshold = float(definition.entry_params["move_threshold"]); rvol_required = float(definition.entry_params["rvol_threshold"]); direction = definition.signal_direction
        climax = None
        for index, bar in enumerate(bars):
            if not (time(9, 10) <= bar.time.time() <= time(14, 50)) or index < 20:
                continue
            prior20 = bars[index - 20:index]; avg = sum(item.volume for item in prior20) / 20
            ret5 = bar.close / bars[index - 5].close - 1 if bars[index - 5].close else 0
            is_climax = (ret5 <= -threshold and bar.volume / avg >= rvol_required and _wick_ratio(bar, upper=False) >= .5) if direction == "LONG" else (ret5 >= threshold and bar.volume / avg >= rvol_required and _wick_ratio(bar, upper=True) >= .5)
            if is_climax:
                climax = bar; break
        if not climax:
            return None
        confirm = next((bar for bar in bars if climax.time < bar.time <= climax.time + timedelta(minutes=8)
                        and ((bar.low >= climax.low and bar.close > max(climax.open, climax.close) and bar.close > bar.open) if direction == "LONG" else (bar.high <= climax.high and bar.close < min(climax.open, climax.close) and bar.close < bar.open))), None)
        target = _next_actual(bars, confirm.time) if confirm else None
        return _decision(definition, DecisionType.ENTRY, confirm.time, target=target, entry_reason="S3_VOLUME_CLIMAX_REVERSAL", levels={"climax_time": climax.time.isoformat()}, evidence={"move_threshold": threshold, "rvol_threshold": rvol_required}) if confirm and target else None

    def _s4(self, definition: StrategyDefinition, bars: Sequence[CompletedBar]) -> SignalDecision | None:
        threshold = float(definition.entry_params["morning_threshold"]); direction = definition.signal_direction
        open_bar = next((bar for bar in bars if time(9, 0) <= bar.time.time() <= time(9, 5)), None)
        before_1100 = [bar for bar in bars if bar.time.time() <= time(11, 0)]
        if not open_bar or not before_1100 or not open_bar.open:
            return None
        morning_return = before_1100[-1].close / open_bar.open - 1
        value = volume = 0.0; vwaps: dict[datetime, float] = {}
        for bar in bars:
            value += ((bar.high + bar.low + bar.close) / 3) * bar.volume; volume += bar.volume; vwaps[bar.time] = value / volume if volume else 0.0
        for index, bar in enumerate(bars):
            if not (time(14, 0) <= bar.time.time() <= time(14, 55)) or index < 5:
                continue
            prior = bars[index - 5:index]
            is_signal = (morning_return >= threshold and bar.close > vwaps[bar.time] and bar.close > max(x.high for x in prior) and bar.close > bar.open) if direction == "LONG" else (morning_return <= -threshold and bar.close < vwaps[bar.time] and bar.close < min(x.low for x in prior) and bar.close < bar.open)
            if is_signal:
                target = _next_actual(bars, bar.time)
                return _decision(definition, DecisionType.ENTRY, bar.time, target=target, entry_reason="S4_AFTERNOON_MOMENTUM", levels={"morning_return": morning_return, "vwap": vwaps[bar.time]}) if target else None
        return None

    @staticmethod
    def _structure_trigger(definition: StrategyDefinition, bars: Sequence[CompletedBar], entry_time: datetime, bars_count: int) -> CompletedBar | None:
        for bar in bars:
            if bar.time < entry_time + timedelta(minutes=5):
                continue
            window = [prior for prior in bars if bar.time - timedelta(minutes=bars_count) <= prior.time < bar.time]
            if not window:
                continue
            if definition.signal_direction == "LONG" and bar.close < min(prior.low for prior in window):
                return bar
            if definition.signal_direction == "SHORT" and bar.close > max(prior.high for prior in window):
                return bar
        return None
