"""PAPER-only V0.3 evaluation orchestration; no broker send path exists here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

from .contracts import SignalEvent, snapshot_payload
from .evaluator import DailyMaStrategy, evaluate_ma, evaluate_strategy
from .execution import first_actual_execution_bar
from .identity import snapshot_hash


@dataclass(frozen=True)
class EvaluationResult:
    strategy_id: str
    event_key: str
    entry_created: bool
    normal_exit_evaluated: bool
    no_execution_bar: bool


class PaperRuntimeRepository(Protocol):
    def canonical_strategies(self) -> Sequence[DailyMaStrategy]: ...
    def entry_event_exists(self, strategy_id: str, event_key: str, snapshot_digest: str) -> bool: ...
    def record_entry(self, *, strategy: DailyMaStrategy, event: SignalEvent, snapshot: dict[str, object],
                     snapshot_digest: str, execution_time: datetime | None, execution_price: float | None) -> bool: ...
    def evaluate_open_normal_exits(self, *, signal_time: datetime) -> int: ...


class DailyMaPaperRuntime:
    """Evaluates all canonical strategies at 15:18 with DB writes delegated."""

    def __init__(self, *, repository: PaperRuntimeRepository, raw_provider) -> None:
        self.repository = repository
        self.raw_provider = raw_provider

    def evaluate_1518(self, at: datetime) -> tuple[EvaluationResult, ...]:
        if at.hour != 15 or at.minute != 18:
            raise ValueError("Daily MA V0.3 only evaluates entry signals at 15:18 KST")
        results: list[EvaluationResult] = []
        # Normal exits deliberately run independently of new entries.
        self.repository.evaluate_open_normal_exits(signal_time=at)
        for strategy in self.repository.canonical_strategies():
            source = self.raw_provider.source_bar(strategy.signal_code, at)
            if source is None:
                continue
            periods = (3, 5, 10, 20, 30, 50, strategy.entry_fast_ma, strategy.entry_slow_ma,
                       strategy.exit_fast_ma, strategy.exit_slow_ma, *( [strategy.trend_ma] if strategy.trend_ma else []))
            prior = self.raw_provider.prior_daily_closes(strategy.signal_code, at.date(), max(periods))
            try:
                ma = evaluate_ma(prior_closes=prior, today_1518_close=float(source["close"]), periods=periods)
            except ValueError:
                continue
            decision = evaluate_strategy(strategy=strategy, ma=ma)
            if not decision.entry:
                continue
            event = SignalEvent(strategy.signal_code, strategy.direction, strategy.entry_fast_ma,
                                strategy.entry_slow_ma, at.date().isoformat(), at)
            snapshot = snapshot_payload(source_bar=source, prior_close_hash=ma.prior_close_hash,
                                        entry_fast_value=ma.values_now[strategy.entry_fast_ma],
                                        entry_slow_value=ma.values_now[strategy.entry_slow_ma],
                                        trend_value=ma.values_now.get(strategy.trend_ma) if strategy.trend_ma else None,
                                        trend_passed=decision.trend_passed, direction=strategy.direction,
                                        venue="KRX", data_source="KIS")
            digest = snapshot_hash(snapshot)
            if self.repository.entry_event_exists(strategy.strategy_id, event.key(), digest):
                results.append(EvaluationResult(strategy.strategy_id, event.key(), False, False, False))
                continue
            execution = first_actual_execution_bar(bars=self.raw_provider.execution_bars(strategy.execution_code, at), signal_time=at)
            created = self.repository.record_entry(strategy=strategy, event=event, snapshot=snapshot,
                                                   snapshot_digest=digest,
                                                   execution_time=execution.time if execution else None,
                                                   execution_price=execution.open_price if execution else None)
            results.append(EvaluationResult(strategy.strategy_id, event.key(), created, False, execution is None))
        return tuple(results)
