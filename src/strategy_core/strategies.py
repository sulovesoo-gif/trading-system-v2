"""Deterministic LIVE-4 entry cores and exit policies.

They emit intent only.  Historical price selection and future broker fills are
intentionally outside this package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .bars import CompletedBar, S1Evidence, S2Evidence
from .contracts import DecisionType, SignalDecision, decision_id
from .registry import StrategyDefinition
from .state import S1State, S2State, S3State


def _decision(definition: StrategyDefinition, kind: DecisionType, at: datetime, *, entry_reason: str | None = None,
              exit_reason: str | None = None, target_time: datetime | None = None, reference_levels=None,
              invalidation_levels=None, required_lookback: int = 0, evidence=None, shared_entry: str | None = None) -> SignalDecision:
    return SignalDecision(
        decision_id=decision_id(strategy_code=definition.strategy_instance, signal_time=at, decision_type=kind, scope=shared_entry or ""),
        strategy_id=definition.strategy_id, strategy_code=definition.strategy_code,
        strategy_version=definition.strategy_version, code_commit=definition.code_commit,
        signal_stock_code=definition.signal_stock_code, signal_direction=definition.signal_direction,
        signal_time=at, execution_stock_code=definition.execution_stock_code,
        execution_direction=definition.execution_direction, decision_type=kind,
        entry_reason=entry_reason, exit_reason=exit_reason, target_time=target_time,
        reference_levels=reference_levels or {}, invalidation_levels=invalidation_levels or {},
        required_lookback=required_lookback, parameter_snapshot=definition.entry_params,
        evidence=evidence or {}, shared_entry_decision_id=shared_entry,
    )


class S1OrPullbackRestart:
    """S1 entry consumes completed-bar feature evidence; state owns pullback_low."""

    def __init__(self, definition: StrategyDefinition, state: S1State | None = None) -> None:
        self.definition, self.state = definition, state or S1State()

    def on_evidence(self, evidence: S1Evidence) -> SignalDecision:
        self.state.or_high, self.state.or_low = evidence.or_high, evidence.or_low
        if evidence.breakout:
            self.state.breakout_time = evidence.bar.time
        if evidence.pullback:
            self.state.pullback_time = evidence.bar.time
            self.state.pullback_low = evidence.pullback_low
        if evidence.restart and self.state.pullback_low is not None:
            self.state.entry_time = evidence.bar.time
            return _decision(self.definition, DecisionType.ENTRY, evidence.bar.time,
                             entry_reason="S1_OR_PULLBACK_RESTART", reference_levels={"or_high": evidence.or_high, "or_low": evidence.or_low, "pullback_low": self.state.pullback_low},
                             invalidation_levels={"pullback_low": self.state.pullback_low}, required_lookback=30,
                             evidence={"breakout": True, "pullback": True, "restart": True})
        return _decision(self.definition, DecisionType.NONE, evidence.bar.time, required_lookback=30)


class PullbackLowBreakWithin30Eod:
    def __init__(self, definition: StrategyDefinition, *, entry_time: datetime, pullback_low: float) -> None:
        self.definition, self.entry_time, self.pullback_low, self.closed = definition, entry_time, pullback_low, False

    def on_completed_source_bar(self, bar: CompletedBar) -> SignalDecision:
        if self.closed:
            return _decision(self.definition, DecisionType.NONE, bar.time)
        deadline = self.entry_time + timedelta(minutes=30)
        if bar.time <= deadline and bar.close < self.pullback_low:
            self.closed = True
            return _decision(self.definition, DecisionType.EXIT, bar.time, exit_reason="PULLBACK_LOW_BREAK_WITHIN30",
                             target_time=bar.time + timedelta(minutes=1), invalidation_levels={"pullback_low": self.pullback_low},
                             evidence={"source_close": bar.close, "within_30_minutes": True})
        if bar.time.time().strftime("%H:%M") == "15:19":
            self.closed = True
            return _decision(self.definition, DecisionType.EXIT, bar.time, exit_reason="EOD_1519", target_time=bar.time,
                             invalidation_levels={"pullback_low": self.pullback_low}, evidence={"eod_target": "15:19"})
        return _decision(self.definition, DecisionType.HOLD, bar.time, invalidation_levels={"pullback_low": self.pullback_low})


class S2FailedOrVwap:
    def __init__(self, definition: StrategyDefinition, state: S2State | None = None) -> None:
        self.definition, self.state = definition, state or S2State()

    def on_evidence(self, evidence: S2Evidence) -> SignalDecision:
        self.state.or_high, self.state.or_low, self.state.vwap = evidence.or_high, evidence.or_low, evidence.vwap
        if evidence.failed_breakout:
            self.state.failed_breakout_time = evidence.bar.time
        if evidence.short_signal:
            self.state.entry_time = evidence.bar.time
            return _decision(self.definition, DecisionType.ENTRY, evidence.bar.time, entry_reason="S2_FAILED_OR_VWAP",
                             reference_levels={"or_high": evidence.or_high, "or_low": evidence.or_low, "vwap": evidence.vwap},
                             required_lookback=30, evidence={"failed_breakout": evidence.failed_breakout, "short_signal": True})
        return _decision(self.definition, DecisionType.NONE, evidence.bar.time, required_lookback=30)


class Fixed30:
    def __init__(self, definition: StrategyDefinition, *, entry_time: datetime) -> None:
        self.definition, self.entry_time, self.closed = definition, entry_time, False

    def on_time(self, at: datetime) -> SignalDecision:
        target = self.entry_time + timedelta(minutes=30)
        if not self.closed and at >= target:
            self.closed = True
            return _decision(self.definition, DecisionType.EXIT, at, exit_reason="FIXED_30", target_time=target,
                             evidence={"entry_time": self.entry_time.isoformat(), "hold_minutes": 30})
        return _decision(self.definition, DecisionType.HOLD, at)


class S3VolumeClimaxReversal:
    """One shared S3 entry signal, independent of the 3BAR/5BAR exit instance."""

    def __init__(self, definition: StrategyDefinition, state: S3State | None = None) -> None:
        self.definition, self.state = definition, state or S3State()

    def on_completed_bar(self, bar: CompletedBar, *, prior_bars: list[CompletedBar], rvol20: float) -> SignalDecision:
        if self.state.entry_signal_time is not None:
            return _decision(self.definition, DecisionType.NONE, bar.time, required_lookback=20)
        if self.state.climax_time is None:
            if len(prior_bars) < 5:
                return _decision(self.definition, DecisionType.NONE, bar.time, required_lookback=20)
            reference = prior_bars[-5].close
            move = (bar.close - reference) / reference if reference else 0.0
            body = abs(bar.close - bar.open)
            upper_wick = bar.high - max(bar.open, bar.close)
            if (bar.time.time().strftime("%H:%M") >= "09:10" and bar.time.time().strftime("%H:%M") <= "14:50"
                    and move >= float(self.definition.entry_params.get("move_threshold", 0.008))
                    and rvol20 >= float(self.definition.entry_params.get("rvol_threshold", 2.0))
                    and body > 0 and upper_wick / body >= 0.5):
                self.state.climax_time, self.state.climax_high = bar.time, bar.high
                self.state.climax_open, self.state.climax_close = bar.open, bar.close
            return _decision(self.definition, DecisionType.NONE, bar.time, required_lookback=20)
        if bar.time > self.state.climax_time + timedelta(minutes=8):
            self.state.climax_time = None
            return _decision(self.definition, DecisionType.NONE, bar.time, required_lookback=20)
        if bar.high <= self.state.climax_high and bar.close < min(self.state.climax_open, self.state.climax_close) and bar.close < bar.open:
            self.state.entry_signal_time = bar.time
            shared = decision_id(strategy_code="S3_VOLUME_CLIMAX_REVERSAL", signal_time=bar.time, decision_type=DecisionType.ENTRY, scope="shared")
            return _decision(self.definition, DecisionType.ENTRY, bar.time, entry_reason="S3_VOLUME_CLIMAX_REVERSAL",
                             target_time=bar.time + timedelta(minutes=1), reference_levels={"climax_high": self.state.climax_high},
                             required_lookback=20, evidence={"rvol20": rvol20, "confirmation_within_minutes": 8}, shared_entry=shared)
        return _decision(self.definition, DecisionType.HOLD, bar.time, required_lookback=20)


@dataclass
class StructureExitMax30Stop25:
    definition: StrategyDefinition
    entry_time: datetime
    entry_price: float
    structure_bars: int
    closed: bool = False

    def on_bars(self, *, signal_bar: CompletedBar, execution_bar: CompletedBar, prior_signal_bars: list[CompletedBar]) -> SignalDecision:
        if self.closed:
            return _decision(self.definition, DecisionType.NONE, signal_bar.time)
        candidates: list[tuple[datetime, int, str]] = []
        if signal_bar.time >= self.entry_time + timedelta(minutes=5) and len(prior_signal_bars) >= self.structure_bars:
            if signal_bar.close > max(bar.high for bar in prior_signal_bars[-self.structure_bars:]):
                candidates.append((execution_bar.time, 0, "STRUCTURE_RECLAIM"))
        if execution_bar.close <= self.entry_price * 0.975:
            candidates.append((execution_bar.time + timedelta(minutes=1), 1, "STOP_2.5"))
        candidates.append((self.entry_time + timedelta(minutes=30), 2, "MAX_30"))
        eligible = [item for item in candidates if item[0] <= execution_bar.time + timedelta(minutes=1)]
        if eligible:
            target, _priority, reason = min(eligible)
            self.closed = True
            return _decision(self.definition, DecisionType.EXIT, signal_bar.time, exit_reason=reason, target_time=target,
                             reference_levels={"entry_price": self.entry_price}, required_lookback=self.structure_bars,
                             evidence={"structure_bars": self.structure_bars, "stop_reference": "EXECUTION_PRODUCT"})
        return _decision(self.definition, DecisionType.HOLD, signal_bar.time, required_lookback=self.structure_bars)
