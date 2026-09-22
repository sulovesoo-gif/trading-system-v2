"""Discovery and state orchestration attached to the existing FLOW socket."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
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
        self._poll_date = None
        self._poll_count = 0
        self._poll_errors = 0
        self._poll_discovered: set[str] = set()
        self._poll_summary_date = None

    def _ensure_poll_date(self, at: datetime) -> None:
        if self._poll_date == at.date():
            return
        self._poll_date = at.date()
        self._poll_count = 0
        self._poll_errors = 0
        self._poll_discovered = set()
        self._poll_summary_date = None

    def restore(self, *, at: datetime) -> None:
        self._ensure_poll_date(at)
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
        self._ensure_poll_date(at)
        self._poll_count += 1
        started = perf_counter()
        seq = self._condition_seq or "UNRESOLVED"
        try:
            seq = self._resolve_seq(at)
            candidates = self.condition_search.candidates(seq)
        except Exception:
            self._poll_errors += 1
            elapsed_ms = round((perf_counter() - started) * 1000)
            LOGGER.info(
                "FIRST_RISE_POLL time=%s condition=%s seq=%s http_status=%s "
                "kis_code=%s result_count=ERROR empty_result=false elapsed_ms=%d",
                at.isoformat(), self.CONDITION_NAME, seq,
                getattr(self.condition_search, "last_http_status", None) or "UNKNOWN",
                getattr(self.condition_search, "last_kis_code", None) or "UNKNOWN",
                elapsed_ms,
            )
            raise
        elapsed_ms = round((perf_counter() - started) * 1000)
        LOGGER.info(
            "FIRST_RISE_POLL time=%s condition=%s seq=%s http_status=%s "
            "kis_code=%s result_count=%d empty_result=%s elapsed_ms=%d",
            at.isoformat(), self.CONDITION_NAME, seq,
            getattr(self.condition_search, "last_http_status", None) or "UNKNOWN",
            getattr(self.condition_search, "last_kis_code", None) or "UNKNOWN",
            len(candidates), str(not candidates).lower(), elapsed_ms,
        )
        self._poll_discovered.update(candidate.stock_code for candidate in candidates)
        try:
            self.repository.record_condition_hits(
                poll_time=at,
                business_date=at.date(),
                condition_name=self.CONDITION_NAME,
                condition_seq=seq,
                candidates=candidates,
            )
        except Exception:
            self._poll_errors += 1
            raise
        created_count = 0
        try:
            for candidate in candidates:
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
                LOGGER.info(
                    "FIRST_RISE_DISCOVERED stock_code=%s stock_name=%s discovered_at=%s",
                    candidate.stock_code, candidate.stock_name or "", at.isoformat(),
                )
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
        except Exception:
            self._poll_errors += 1
            raise
        return created_count

    def _log_poll_summary_once(self, at: datetime) -> None:
        self._ensure_poll_date(at)
        if at.time() <= self.SEARCH_END or self._poll_summary_date == at.date():
            return
        LOGGER.info(
            "FIRST_RISE_POLL_SUMMARY date=%s poll_count=%d discovered_unique=%d errors=%d",
            at.date().isoformat(), self._poll_count, len(self._poll_discovered), self._poll_errors,
        )
        self._poll_summary_date = at.date()

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
        if at.time() <= self.SEARCH_END:
            return 0
        if self._expired_date == at.date():
            self._log_poll_summary_once(at)
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
        self._log_poll_summary_once(at)
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
