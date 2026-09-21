"""Discovery and state orchestration attached to the existing FLOW socket."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from decimal import Decimal
from threading import RLock
from zoneinfo import ZoneInfo

from .models import CandidateState, Observation, ResearchState, TERMINAL_STATES

LOGGER = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


class DynamicExecutionRegistry:
    """Owner-scoped H0STCNT0 demand for the one existing websocket."""

    def __init__(self) -> None:
        self._owners_by_symbol: dict[str, set[str]] = {}
        self._lock = RLock()

    def add(self, stock_code: str, *, owner: str) -> None:
        with self._lock:
            self._owners_by_symbol.setdefault(stock_code, set()).add(owner)

    def discard(self, stock_code: str, *, owner: str) -> None:
        with self._lock:
            owners = self._owners_by_symbol.get(stock_code)
            if owners is None:
                return
            owners.discard(owner)
            if not owners:
                self._owners_by_symbol.pop(stock_code, None)

    def symbols(self, *, owner: str | None = None) -> set[str]:
        with self._lock:
            if owner is None:
                return set(self._owners_by_symbol)
            return {
                stock_code for stock_code, owners in self._owners_by_symbol.items()
                if owner in owners
            }

    def discard_owner(self, owner: str) -> None:
        with self._lock:
            for stock_code in list(self._owners_by_symbol):
                owners = self._owners_by_symbol[stock_code]
                owners.discard(owner)
                if not owners:
                    self._owners_by_symbol.pop(stock_code, None)


class FirstRiseBreakoutRuntime:
    CONDITION_NAME = "TSV2_오전1차상승후돌파_후보_V1"
    SUBSCRIPTION_OWNER = "first_rise_breakout"
    SEARCH_START = time(9, 1)
    SEARCH_END = time(10, 0)

    def __init__(
        self, *, repository, strategy, condition_search, minute_source,
        subscriptions: DynamicExecutionRegistry, now_provider=None,
        scan_interval_seconds: int = 60,
    ) -> None:
        self.repository = repository
        self.strategy = strategy
        self.condition_search = condition_search
        self.minute_source = minute_source
        self.subscriptions = subscriptions
        self.now = now_provider or (lambda: datetime.now(KST).replace(tzinfo=None))
        self.scan_interval_seconds = scan_interval_seconds
        self._states: dict[str, CandidateState] = {}
        self._condition_date = None
        self._condition_seq: str | None = None
        self._expired_date = None
        self._state_lock = RLock()
        self._restored_date = None

    def restore(self, *, at: datetime) -> None:
        restored = {state.stock_code: state for state in self.repository.active_states(business_date=at.date())}
        with self._state_lock:
            self._states = restored
            self._restored_date = at.date()
        self.subscriptions.discard_owner(self.SUBSCRIPTION_OWNER)
        for state in restored.values():
            if state.state not in TERMINAL_STATES:
                self.subscriptions.add(state.stock_code, owner=self.SUBSCRIPTION_OWNER)

    def _resolve_seq(self, at: datetime) -> str:
        if self._condition_date != at.date() or not self._condition_seq:
            self._condition_seq = self.condition_search.resolve_seq(self.CONDITION_NAME)
            self._condition_date = at.date()
            LOGGER.info("first-rise saved condition resolved name=%s seq=%s", self.CONDITION_NAME, self._condition_seq)
        return self._condition_seq

    def scan_once(self, *, at: datetime) -> int:
        if not (self.SEARCH_START <= at.time() <= self.SEARCH_END):
            return 0
        seq = self._resolve_seq(at)
        created_count = 0
        for candidate in self.condition_search.candidates(seq):
            state, created = self.repository.record_candidate(
                business_date=at.date(), condition_name=self.CONDITION_NAME, condition_seq=seq,
                stock_code=candidate.stock_code, stock_name=candidate.stock_name,
                discovered_at=at, raw_payload=candidate.raw_payload,
            )
            with self._state_lock:
                self._states[candidate.stock_code] = state
            self.subscriptions.add(candidate.stock_code, owner=self.SUBSCRIPTION_OWNER)
            if not created:
                continue
            created_count += 1
            peak = self.minute_source.peak(stock_code=candidate.stock_code, until=at)
            if peak is None:
                LOGGER.warning("first-rise peak seed unavailable stock_code=%s", candidate.stock_code)
                continue
            peak_price, peak_time, latest_close = peak
            decision = self.strategy.seed_peak(state, peak_price=peak_price, peak_time=peak_time)
            state = self.repository.apply(
                decision, Observation(peak_time, peak_price, "KIS_1MIN_HIGH"),
                evidence={"source": "KIS_1MIN", "seeded_at": at.isoformat()},
            )
            with self._state_lock:
                self._states[candidate.stock_code] = state
            if latest_close < peak_price:
                self.observe(candidate.stock_code, observed_at=at, price=latest_close, source="KIS_1MIN_CLOSE")
        return created_count

    def observe(self, stock_code: str, *, observed_at: datetime, price: Decimal, source: str = "H0STCNT0") -> None:
        with self._state_lock:
            state = self._states.get(stock_code)
        if state is None:
            return
        observation = Observation(observed_at, price, source)
        decision = self.strategy.observe(state, observation)
        if decision.changed:
            state = self.repository.apply(decision, observation)
            with self._state_lock:
                self._states[stock_code] = state
            if state.state in {ResearchState.REJECTED, ResearchState.EXPIRED, ResearchState.PAPER_EXITED}:
                self.subscriptions.discard(stock_code, owner=self.SUBSCRIPTION_OWNER)

    def expire_once(self, *, at: datetime) -> int:
        if at.time() <= self.SEARCH_END or self._expired_date == at.date():
            return 0
        count = 0
        with self._state_lock:
            current_states = list(self._states.items())
        for stock_code, state in current_states:
            decision = self.strategy.expire(state, at=at)
            if decision.changed:
                updated = self.repository.apply(decision, Observation(at, state.last_observed_price or Decimal("0"), "CLOCK"))
                with self._state_lock:
                    self._states[stock_code] = updated
                self.subscriptions.discard(stock_code, owner=self.SUBSCRIPTION_OWNER)
                count += 1
        self._expired_date = at.date()
        return count

    def record_exit(self, stock_code: str, *, observed_at: datetime, price: Decimal, reason: str) -> bool:
        with self._state_lock:
            state = self._states.get(stock_code)
        if state is None:
            return False
        observation = Observation(observed_at, price, "EXPLICIT_RESEARCH_EXIT")
        decision = self.strategy.exit(state, observation, reason=reason)
        if not decision.changed:
            return False
        with self._state_lock:
            self._states[stock_code] = self.repository.apply(decision, observation)
        self.subscriptions.discard(stock_code, owner=self.SUBSCRIPTION_OWNER)
        return True

    async def run_forever(self) -> None:
        while True:
            at = self.now()
            try:
                if self._restored_date != at.date():
                    await asyncio.to_thread(self.restore, at=at)
                await asyncio.to_thread(self.scan_once, at=at)
                await asyncio.to_thread(self.expire_once, at=at)
            except Exception:
                # Research discovery must never stop the shared RAW collector.
                LOGGER.exception("first-rise research cycle failed")
            await asyncio.sleep(self.scan_interval_seconds)
