"""REST completed-minute discovery and PAPER state orchestration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from time import perf_counter
from datetime import datetime, time, timedelta
from decimal import Decimal
from threading import RLock
from zoneinfo import ZoneInfo

from .models import CandidateState, MinuteBar, Observation, ResearchState, TERMINAL_STATES

LOGGER = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
FIRST_RISE_EXCLUDED_STOCK_CODES = frozenset({"005930", "000660"})


class FirstRiseBreakoutRuntime:
    CONDITION_NAME = "TSV2_오전1차상승후돌파_후보_V1"
    SEARCH_START = time(9, 1)
    SEARCH_END = time(10, 0)

    def __init__(
        self, *, repository, strategy, condition_search, minute_source,
        now_provider=None,
        scan_interval_seconds: int = 60,
        config=None,
        trading_day=None,
    ) -> None:
        self.repository = repository
        self.trading_day = trading_day
        self.strategy = strategy
        self.now = now_provider or (lambda: datetime.now(KST).replace(tzinfo=None))
        self._config_date = None
        self._config_override = config
        self.config = config
        self._load_daily_config(at=self.now())
        self.condition_search = condition_search
        self.minute_source = minute_source
        raw_factory = getattr(repository, 'completed_minute_raw_repository', None)
        if raw_factory is not None:
            self.minute_source.raw_repository = raw_factory()
        j_factory = getattr(repository, 'j_market_repository', None)
        self.j_market = None
        if j_factory is not None:
            from .j_runtime import JMarketRuntime
            self.j_market = JMarketRuntime(repository=j_factory(),minute_source=self.minute_source)
            if self.config is not None:
                self.SEARCH_START=min(self.config.paper_entry_start,self.config.live_entry_start)
                self.SEARCH_END=max(self.config.paper_entry_cutoff,self.config.live_entry_cutoff)
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
        self._discovered_at = {}

    def _load_daily_config(self, *, at: datetime) -> None:
        if self._config_date == at.date():
            return
        self._config_date = at.date()
        self.config = self._config_override
        if self.config is None:
            try:
                daily_loader = getattr(self.repository, "runtime_config_for_day", None)
                self.config = (daily_loader(at=at) if daily_loader is not None
                               else self.repository.runtime_config())
            except Exception:
                LOGGER.exception("FIRST_RISE_CONFIG_ERROR new ENTRY blocked; OPEN EXIT remains active")
        if self.config is not None:
            self.SEARCH_START = self.strategy.ENTRY_START = self.config.paper_entry_start
            self.SEARCH_END = self.strategy.ENTRY_CUTOFF = self.config.paper_entry_cutoff
            if getattr(self,'j_market',None) is not None:
                self.SEARCH_START=min(self.config.paper_entry_start,self.config.live_entry_start)
                self.SEARCH_END=max(self.config.paper_entry_cutoff,self.config.live_entry_cutoff)
            LOGGER.info("FIRST_RISE_CONFIG_LOADED date=%s paper_start=%s paper_cutoff=%s live_start=%s live_cutoff=%s",
                        at.date(), self.config.paper_entry_start, self.config.paper_entry_cutoff,
                        self.config.live_entry_start, self.config.live_entry_cutoff)

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
        if self.j_market is not None:
            self.j_market.restore(at=at)
            restored={code:s for code,s in restored.items() if s.state==ResearchState.PAPER_ENTERED}
        loader = getattr(self.repository, 'discovery_times', None)
        self._discovered_at = loader(business_date=at.date()) if loader else {}
        with self._state_lock:
            self._states = restored
            self._restored_date = at.date()

    def _resolve_seq(self, at: datetime) -> str:
        if self._condition_date != at.date() or not self._condition_seq:
            self._condition_seq = self.condition_search.resolve_seq(self.CONDITION_NAME)
            self._condition_date = at.date()
            LOGGER.info("first-rise saved condition resolved name=%s seq=%s", self.CONDITION_NAME, self._condition_seq)
        return self._condition_seq

    def scan_once(self, *, at: datetime) -> int:
        if self.trading_day is not None and not self.trading_day(at):
            return 0
        if self.config is None or not (self.SEARCH_START <= at.time() < self.SEARCH_END):
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
                if candidate.stock_code in FIRST_RISE_EXCLUDED_STOCK_CODES:
                    continue
                state, created = self.repository.record_candidate(
                    business_date=at.date(), condition_name=self.CONDITION_NAME, condition_seq=seq,
                    stock_code=candidate.stock_code, stock_name=candidate.stock_name,
                    discovered_at=at, raw_payload=candidate.raw_payload,
                )
                with self._state_lock:
                    if self.j_market is None or state.state==ResearchState.PAPER_ENTERED:
                        self._states[candidate.stock_code] = state
                self._discovered_at.setdefault(candidate.stock_code, at)
                if not created:
                    continue
                created_count += 1
                LOGGER.info(
                    "FIRST_RISE_DISCOVERED stock_code=%s stock_name=%s discovered_at=%s",
                    candidate.stock_code, candidate.stock_name or "", at.isoformat(),
                )
                if self.j_market is not None:
                    self.j_market.register(state,discovered_at=at)
                    continue
                bars = self.minute_source.completed_bars_from_open(
                    stock_code=candidate.stock_code, as_of=at,
                )
                previous_close = self._previous_close(candidate.stock_code, at)
                if previous_close is None:
                    LOGGER.warning(
                        "first-rise previous regular close unavailable stock_code=%s",
                        candidate.stock_code,
                    )
                    continue
                session_volume = 0
                for bar in bars:
                    session_volume += bar.volume
                    decision = self.strategy.observe_bar(
                        state, bar, previous_close=previous_close,
                        allow_entry=False, bootstrap=True,
                        session_volume=session_volume,
                        session_amount=bar.accumulated_amount,
                    )
                    state = self._persist_decision(decision, bar)
                with self._state_lock:
                    self._states[candidate.stock_code] = state
        except Exception:
            self._poll_errors += 1
            raise
        return created_count

    def _log_poll_summary_once(self, at: datetime) -> None:
        self._ensure_poll_date(at)
        if self.config is None or at.time() < self.SEARCH_END or self._poll_summary_date == at.date():
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
        # Auxiliary observations never replace completed-minute decisions.
        if decision.changed:
            with self._state_lock:
                self._states[stock_code] = self.repository.apply(decision, observation)

    def refresh_completed_bars(self, *, at: datetime) -> int:
        """Advance every active candidate from actual completed KRX minute bars."""
        if self.trading_day is not None and not self.trading_day(at):
            return 0
        changed = 0
        started = perf_counter()
        calls_before = getattr(self.minute_source, "request_count", 0)
        modes_before = dict(getattr(self.minute_source, "mode_counts", {}))
        rest_targets = rest_errors = 0
        with self._state_lock:
            current_states = list(self._states.items())
        current_states.sort(key=lambda item: item[1].state != ResearchState.PAPER_ENTERED)
        for stock_code, state in current_states:
            if state.state in TERMINAL_STATES:
                continue
            try:
                if state.state != ResearchState.PAPER_ENTERED:
                    if self.config is None:
                        continue
                    if stock_code in FIRST_RISE_EXCLUDED_STOCK_CODES:
                        continue
                if (
                    state.state == ResearchState.PAPER_ENTERED
                    and (state.raw_entry_price is None or state.entry_signal_time is None)
                ):
                    restored = self.repository.open_trade_state(
                        candidate_event_id=state.candidate_event_id,
                    )
                    if restored is None:
                        LOGGER.error(
                            "first-rise lifecycle mismatch stock_code=%s candidate_event_id=%s "
                            "state=%s missing_open_paper_trade=true",
                            stock_code, state.candidate_event_id, state.state.value,
                        )
                        continue
                    state = restored
                    with self._state_lock:
                        self._states[stock_code] = state
                rest_targets += 1
                try:
                    bars = self.minute_source.completed_bars_from_open(stock_code=stock_code, as_of=at)
                except Exception:
                    rest_errors += 1
                    raise
                if state.state == ResearchState.PAPER_ENTERED:
                    decision = self.strategy.exit_from_completed_bars(
                        state, bars, session_ended=at.time() > self.strategy.SESSION_CLOSE,
                    )
                    if decision.changed:
                        state = self._persist_decision(decision, None)
                        changed += 1
                else:
                    previous_close = self._previous_close(stock_code, at)
                    if previous_close is None:
                        continue
                    session_volume = 0
                    for bar in bars:
                        session_volume += bar.volume
                        if state.last_observed_at is not None and bar.bar_time <= state.last_observed_at:
                            continue
                        discovered = self._discovered_at.get(stock_code)
                        bootstrap = discovered is not None and bar.bar_time < discovered.replace(second=0,microsecond=0)
                        decision = self.strategy.observe_bar(
                            state, bar, previous_close=previous_close,
                            allow_entry=not bootstrap, bootstrap=bootstrap,
                            session_volume=session_volume,
                            session_amount=bar.accumulated_amount,
                        )
                        if decision.changed:
                            state = self._persist_decision(decision, bar)
                            changed += 1
                        if state.state in TERMINAL_STATES or state.state == ResearchState.PAPER_ENTERED:
                            break
                    if at.time() >= self.SEARCH_END:
                        decision = self.strategy.expire(state, at=at)
                        if decision.changed:
                            state = self.repository.apply(decision, Observation(at, state.last_observed_price or Decimal("0"), "CLOCK"))
                            changed += 1
                with self._state_lock:
                    self._states[stock_code] = state
                if state.state in TERMINAL_STATES:
                    self._release_terminal(state)
            except Exception as error:
                LOGGER.exception(
                    "first-rise candidate refresh failed stock_code=%s candidate_event_id=%s "
                    "state=%s exception_type=%s error=%s",
                    stock_code, state.candidate_event_id, state.state.value,
                    type(error).__name__, error,
                )
        if self.j_market is not None:
            changed+=self.j_market.refresh(at=at,config=self.config)
            rest_targets+=self.j_market.last_summary.get('targets',0)
            rest_errors+=self.j_market.last_summary.get('errors',0)
        j_summary=self.j_market.last_summary if self.j_market is not None else {}
        modes = getattr(self.minute_source, "mode_counts", {})
        LOGGER.info(
            "FIRST_RISE_REFRESH active=%d paper_entered=%d minute_rest_targets=%d "
            "fhk_calls=%d bootstrap=%d catch_up=%d incremental=%d rest_errors=%d elapsed_ms=%d",
            sum(s.state not in TERMINAL_STATES for _, s in current_states)+j_summary.get('active',0),
            sum(s.state == ResearchState.PAPER_ENTERED for _, s in current_states)+j_summary.get('open',0),
            rest_targets, getattr(self.minute_source, "request_count", 0) - calls_before,
            *(modes.get(key, 0) - modes_before.get(key, 0) for key in ("bootstrap", "catch_up", "incremental")),
            rest_errors, round((perf_counter() - started) * 1000),
        )
        return changed

    def _release_terminal(self, state):
        if state.state not in TERMINAL_STATES:
            return
        if hasattr(self.minute_source, "discard"):
            self.minute_source.discard(stock_code=state.stock_code, business_date=state.business_date)

    def _previous_close(self, stock_code: str, at: datetime) -> Decimal | None:
        reader = getattr(self.minute_source, 'previous_close', None)
        value = reader(stock_code=stock_code,business_date=at.date()) if reader else None
        if value is None:
            LOGGER.error('FIRST_RISE_PREVIOUS_CLOSE_UNAVAILABLE stock_code=%s source=FHKST03010200 entry_blocked=true',stock_code)
        return value

    def _persist_decision(self, decision, bar: MinuteBar | None) -> CandidateState:
        if not decision.changed:
            return decision.before
        evidence = dict(decision.evidence or {})
        if decision.create_entry and self.config is not None:
            evidence.update(self.config.evidence())
            evidence["baseline_strategy_version"] = self.strategy.STRATEGY_VERSION
            decision = replace(decision, evidence=evidence)
        raw_price = decision.raw_execution_price
        if bar is not None:
            observed_at = bar.bar_time
            observed_price = raw_price or bar.close_price
            source = bar.source
        else:
            observed_at = datetime.fromisoformat(evidence["bar_time"])
            observed_price = raw_price or Decimal(evidence["close"])
            source = evidence.get("source", "KIS_1MIN")
        return self.repository.apply(
            decision, Observation(observed_at, observed_price, source), evidence=evidence,
        )

    def expire_once(self, *, at: datetime) -> int:
        if self.trading_day is not None and not self.trading_day(at):
            return 0
        if self.config is None or at.time() < self.SEARCH_END:
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
                self._release_terminal(updated)
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
        self._release_terminal(self._states[stock_code])
        return True

    async def run_forever(self) -> None:
        while True:
            at = self.now()
            try:
                if self.trading_day is not None and not await asyncio.to_thread(self.trading_day, at):
                    await asyncio.sleep(self.scan_interval_seconds)
                    continue
                await asyncio.to_thread(self._load_daily_config, at=at)
                if self._restored_date != at.date():
                    await asyncio.to_thread(self.restore, at=at)
                await asyncio.to_thread(self.scan_once, at=at)
                await asyncio.to_thread(self.refresh_completed_bars, at=at)
                await asyncio.to_thread(self.expire_once, at=at)
            except Exception:
                # Research discovery must never stop the shared RAW collector.
                LOGGER.exception("first-rise research cycle failed")
            await asyncio.sleep(self.scan_interval_seconds)
