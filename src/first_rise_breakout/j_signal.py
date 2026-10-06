"""J market lifecycle, deliberately independent of PAPER fills and broker state."""
from dataclasses import dataclass, replace
from datetime import datetime, time

from .models import CandidateState, Decision, ResearchState
from .strategy import FirstRiseBreakoutStrategy
from .v2_capacity import V2Strategy, FORMULA_VERSION, recent_liquidity

VERSION = FORMULA_VERSION
J_BOUNDARY = time(10)


@dataclass(frozen=True)
class JState:
    tracking: CandidateState
    sequence: int = 0
    open_signal: CandidateState | None = None
    prior_signal_key: str | None = None
    prior_exit_reason: str | None = None
    prior_exit_time: datetime | None = None


@dataclass(frozen=True)
class JStep:
    state: JState
    market_entry: Decision | None = None
    market_exit: Decision | None = None
    observation: Decision | None = None


def entry_sequence(state: JState, signal_time: datetime, *, start: time, cutoff: time):
    if not start <= signal_time.time() < cutoff:
        return None
    if state.open_signal is not None:
        return None
    if state.sequence == 0:
        return 1 if signal_time.time() <= J_BOUNDARY else None
    if (state.sequence == 1 and state.prior_exit_reason == "STOP_ENTRY_BREAK"
            and state.prior_exit_time is not None and signal_time > state.prior_exit_time
            and signal_time.time() >= J_BOUNDARY):
        return 2
    return None


class JSignalEngine:
    """Reuse frozen OHLC/EXIT calculations; only sequence/windows are new.

    Caller supplies completed bars only, ascending, and persists each JStep
    atomically. A skipped/rejected actual order is never input to this engine.
    """
    def __init__(self, config, *, historical_v13=False):
        self.config = config
        self.formula = FirstRiseBreakoutStrategy() if historical_v13 else V2Strategy()
        self.formula_version = 'FIRST_RISE_J_V1.3' if historical_v13 else FORMULA_VERSION
        # 09:00 contributes to peak calculations, but entry_sequence forbids BUY.
        self.formula.ENTRY_START = time(9)
        self.formula.ENTRY_CUTOFF = max(config.paper_entry_cutoff, config.live_entry_cutoff)

    def advance(self, state, bar, *, previous_close=None, completed_bars, bootstrap=False):
        if state.tracking.last_observed_at is not None and bar.bar_time <= state.tracking.last_observed_at:
            return JStep(state)
        usable = [b for b in completed_bars if b.bar_time <= bar.bar_time]
        exited = None
        was_open = state.open_signal is not None
        if was_open:
            decision = self.formula.exit_from_completed_bars(state.open_signal, usable)
            if decision.create_exit:
                exited = replace(decision,evidence={**(decision.evidence or {}),
                    'exit_liquidity':recent_liquidity(usable,signal_time=decision.after.last_observed_at)})
                state = replace(state, open_signal=None,
                    prior_exit_reason=decision.reason,
                    prior_exit_time=decision.after.last_observed_at)

        start = min(self.config.paper_entry_start, self.config.live_entry_start)
        cutoff = max(self.config.paper_entry_cutoff, self.config.live_entry_cutoff)
        sequence = entry_sequence(state, bar.bar_time, start=start, cutoff=cutoff)
        # Historical market events restore sequence, never authorize retro fills.
        eligible = sequence is not None and not was_open
        decision = self.formula.observe_bar(state.tracking, bar,
            previous_close=previous_close, allow_entry=eligible, bootstrap=bootstrap,
            session_volume=sum(b.volume for b in usable), session_amount=bar.accumulated_amount)
        tracking = decision.after
        if decision.create_entry:
            evidence = dict(decision.evidence or {})
            evidence.update(self.config.evidence())
            evidence.update(formula_version=self.formula_version,
                entry_liquidity=recent_liquidity(usable,signal_time=bar.bar_time))
            evidence.update(strategy_version=self.formula_version, signal_sequence=sequence,
                signal_classification="FIRST" if sequence == 1 else "SECOND",
                prior_market_signal_key=state.prior_signal_key,
                prior_market_exit_reason=state.prior_exit_reason,
                second_signal_eligibility_reason="FIRST_INDEPENDENT_STOP" if sequence == 2 else None,
                sequence_replay_only=bootstrap,
                paper_entry_eligible=not bootstrap and self.config.paper_entry_start <= bar.bar_time.time() < self.config.paper_entry_cutoff,
                live_entry_eligible=not bootstrap and self.config.live_entry_start <= bar.bar_time.time() < self.config.live_entry_cutoff)
            entry = replace(decision, evidence=evidence)
            # Continue running-high observation while the independent signal is OPEN.
            tracking = self.formula.seed_peak(state.tracking,
                peak_price=bar.high_price, peak_time=bar.bar_time).after
            state = replace(state, tracking=tracking, sequence=sequence,
                open_signal=decision.after, prior_signal_key=decision.after.entry_event_key,
                prior_exit_reason=None, prior_exit_time=None)
            return JStep(state, market_entry=entry, market_exit=exited)
        if decision.reason == "MISSED_BEFORE_DISCOVERY" and not bootstrap:
            decision = replace(decision, reason="J_SEQUENCE_NOT_ELIGIBLE",
                evidence={**(decision.evidence or {}), "missed_before_discovery": False,
                          "signal_sequence_used": state.sequence})
        return JStep(replace(state, tracking=tracking), market_exit=exited, observation=decision)

    def session_close(self, state, completed_bars, *, at):
        if state.open_signal is None or at.time() <= self.formula.SESSION_CLOSE:
            return JStep(state)
        bars = [b for b in completed_bars if b.bar_time.time() <= self.formula.SESSION_CLOSE
                and b.bar_time < at.replace(second=0, microsecond=0)]
        exit_decision = self.formula.exit_from_completed_bars(state.open_signal, bars, session_ended=True)
        if not exit_decision.create_exit:
            return JStep(state)
        exit_decision = replace(exit_decision,evidence={**(exit_decision.evidence or {}),
            'exit_liquidity':recent_liquidity(bars,signal_time=exit_decision.after.last_observed_at)})
        return JStep(replace(state, open_signal=None, prior_exit_reason=exit_decision.reason,
            prior_exit_time=exit_decision.after.last_observed_at), market_exit=exit_decision)
