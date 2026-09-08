"""FLOW V3 completed-minute PAPER orchestration (no LIVE execution path)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .engine import FlowV3SignalEngine


@dataclass(frozen=True)
class RuntimeCycleResult:
    lock_acquired: bool
    completed_minutes: int = 0
    entry_events_created: int = 0
    entry_lots_created: int = 0
    exit_signals_recorded: int = 0
    normal_exits_closed: int = 0
    forced_eod_closed: int = 0


class FlowV3PaperRuntime:
    """Sequentially consume durable completed minutes with restart-safe cursors."""

    def __init__(self, repository, *, engine: FlowV3SignalEngine | None = None) -> None:
        self.repository = repository
        self.engine = engine or FlowV3SignalEngine()

    def run_cycle(self, *, now: datetime) -> RuntimeCycleResult:
        with self.repository.cycle_lock() as acquired:
            if not acquired:
                return RuntimeCycleResult(lock_acquired=False)
            completed = entry_events = exit_signals = 0
            strategy_cache = {}
            for stock_code, bar_time in self.repository.completed_minutes(now=now):
                strategies = strategy_cache.setdefault(
                    stock_code, self.repository.strategies(stock_code=stock_code)
                )
                history = self.repository.recent_states(
                    stock_code=stock_code,business_date=bar_time.date(),before=bar_time
                )
                base = self.repository.minute_base(stock_code=stock_code,bar_time=bar_time)
                state = self.engine.build_state(base=base,history=history)
                entries = self.engine.entry_signals(
                    state=state,recent_states=history,strategies=strategies
                )
                created, exits = self.repository.persist_minute(
                    state=state,entry_signals=entries
                )
                completed += 1
                entry_events += created
                exit_signals += exits
            entry_lots = self.repository.resolve_pending_entries(now=now)
            normal_exits = self.repository.resolve_pending_exits()
            forced_eod = self.repository.finalize_eod(now=now)
            return RuntimeCycleResult(
                lock_acquired=True,
                completed_minutes=completed,
                entry_events_created=entry_events,
                entry_lots_created=entry_lots,
                exit_signals_recorded=exit_signals,
                normal_exits_closed=normal_exits,
                forced_eod_closed=forced_eod,
            )
