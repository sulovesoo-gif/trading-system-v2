"""Deterministic candidate-state decisions; no broker or LIVE concepts."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from .models import CandidateState, Decision, Observation, ResearchState, TERMINAL_STATES


class FirstRiseBreakoutStrategy:
    ENTRY_CUTOFF = time(10, 0)
    MIN_PULLBACK = Decimal("0.005")
    MAX_PULLBACK = Decimal("0.04")
    MIN_PEAK_AGE = timedelta(minutes=9)

    def seed_peak(self, state: CandidateState, *, peak_price: Decimal, peak_time: datetime) -> Decision:
        if peak_price <= 0:
            raise ValueError("peak_price must be positive")
        after = state.evolve(
            state=ResearchState.TRACKING,
            peak_price=peak_price,
            peak_time=peak_time,
            pullback_low_price=None,
            pullback_pct=None,
            last_observed_at=peak_time,
            last_observed_price=peak_price,
        )
        return Decision(state, after, "ACTUAL_SESSION_PEAK_SEEDED")

    def observe(self, state: CandidateState, observation: Observation) -> Decision:
        if observation.price <= 0:
            raise ValueError("observation price must be positive")
        if state.state in TERMINAL_STATES or state.state == ResearchState.PAPER_ENTERED:
            return Decision(state, state, "STATE_NOT_ENTRY_ELIGIBLE")
        if observation.observed_at.date() != state.business_date:
            return Decision(state, state, "DIFFERENT_BUSINESS_DATE")
        if observation.observed_at.time() > self.ENTRY_CUTOFF:
            after = state.evolve(
                state=ResearchState.EXPIRED,
                last_observed_at=observation.observed_at,
                last_observed_price=observation.price,
            )
            return Decision(state, after, "ENTRY_CUTOFF_EXPIRED")
        if state.peak_price is None or state.peak_time is None:
            return self.seed_peak(state, peak_price=observation.price, peak_time=observation.observed_at)

        peak, peak_time = state.peak_price, state.peak_time
        pulled_back = state.state in {ResearchState.PULLBACK, ResearchState.WAIT_REBREAK}
        if observation.price > peak:
            if pulled_back and observation.observed_at - peak_time >= self.MIN_PEAK_AGE:
                key = f"FRB|{state.business_date.isoformat()}|{state.stock_code}|{peak_time.isoformat()}|{observation.observed_at.isoformat()}"
                after = state.evolve(
                    state=ResearchState.PAPER_ENTERED,
                    last_observed_at=observation.observed_at,
                    last_observed_price=observation.price,
                    entry_event_key=key,
                )
                return Decision(state, after, "SAME_PEAK_REBREAK_CONFIRMED", create_entry=True)
            after = state.evolve(
                state=ResearchState.TRACKING,
                peak_price=observation.price,
                peak_time=observation.observed_at,
                pullback_low_price=None,
                pullback_pct=None,
                last_observed_at=observation.observed_at,
                last_observed_price=observation.price,
            )
            return Decision(state, after, "NEW_PEAK_RESET")

        pullback = (peak - observation.price) / peak
        if pullback > self.MAX_PULLBACK:
            after = state.evolve(
                state=ResearchState.REJECTED,
                pullback_low_price=observation.price,
                pullback_pct=pullback,
                last_observed_at=observation.observed_at,
                last_observed_price=observation.price,
            )
            return Decision(state, after, "PULLBACK_OVER_4_PERCENT")
        if pullback >= self.MIN_PULLBACK:
            low = min(filter(lambda value: value is not None, (state.pullback_low_price, observation.price)))
            next_state = ResearchState.PULLBACK if not pulled_back else ResearchState.WAIT_REBREAK
            after = state.evolve(
                state=next_state,
                pullback_low_price=low,
                pullback_pct=(peak - low) / peak,
                last_observed_at=observation.observed_at,
                last_observed_price=observation.price,
            )
            return Decision(state, after, "VALID_PULLBACK_OBSERVED" if not pulled_back else "WAITING_SAME_PEAK_REBREAK")
        after = state.evolve(
            last_observed_at=observation.observed_at,
            last_observed_price=observation.price,
        )
        return Decision(state, after, "TRACKING_WITHIN_PULLBACK_THRESHOLD")

    def expire(self, state: CandidateState, *, at: datetime) -> Decision:
        if state.state in TERMINAL_STATES or state.state == ResearchState.PAPER_ENTERED:
            return Decision(state, state, "STATE_NOT_EXPIRABLE")
        return Decision(state, state.evolve(state=ResearchState.EXPIRED, last_observed_at=at), "ENTRY_CUTOFF_EXPIRED")

    def exit(self, state: CandidateState, observation: Observation, *, reason: str) -> Decision:
        if state.state != ResearchState.PAPER_ENTERED:
            return Decision(state, state, "NO_OPEN_PAPER_TRADE")
        after = state.evolve(
            state=ResearchState.PAPER_EXITED,
            last_observed_at=observation.observed_at,
            last_observed_price=observation.price,
        )
        return Decision(state, after, reason, create_exit=True)
