"""PAPER-only V0.3 evaluation orchestration; no broker send path exists here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Protocol, Sequence

from .contracts import SignalEvent, snapshot_payload
from .evaluator import DailyMaStrategy, evaluate_ma, evaluate_strategy
from .evaluator import day20_triggered
from .execution import first_actual_bar_after, first_actual_execution_bar
from .identity import snapshot_hash


@dataclass(frozen=True)
class EvaluationResult:
    strategy_id: str
    event_key: str
    entry_created: bool
    normal_exit_evaluated: bool
    no_execution_bar: bool


@dataclass(frozen=True)
class OpenNormalTrade:
    paper_trade_id: int
    entry_signal_date: object
    strategy: DailyMaStrategy


@dataclass(frozen=True)
class OpenDay20Trade:
    paper_trade_id: int
    strategy: DailyMaStrategy


class PaperRuntimeRepository(Protocol):
    def canonical_strategies(self) -> Sequence[DailyMaStrategy]: ...
    def entry_event_exists(self, strategy_id: str, event_key: str, snapshot_digest: str) -> bool: ...
    def record_entry(self, *, strategy: DailyMaStrategy, event: SignalEvent, snapshot: dict[str, object],
                     snapshot_digest: str, execution_time: datetime | None, execution_price: float | None) -> bool: ...
    def open_normal_tracking_trades(self) -> Sequence[OpenNormalTrade]: ...
    def record_normal_exit(self, *, paper_trade_id: int, signal_time: datetime,
                           execution_time: datetime, execution_price: float) -> bool: ...
    def open_day20_trades(self) -> Sequence[OpenDay20Trade]: ...
    def record_day20_exit(self, *, paper_trade_id: int, trigger_time: datetime,
                          execution_time: datetime, execution_price: float) -> bool: ...


class DailyMaPaperRuntime:
    """Evaluates all canonical strategies at 15:18 with DB writes delegated."""

    def __init__(self, *, repository: PaperRuntimeRepository, raw_provider) -> None:
        self.repository = repository
        self.raw_provider = raw_provider

    def evaluate_1518(self, at: datetime) -> tuple[EvaluationResult, ...]:
        if at.hour != 15 or at.minute != 18:
            raise ValueError("Daily MA V0.3 only evaluates entry signals at 15:18 KST")
        results: list[EvaluationResult] = []
        # Normal exits deliberately run independently of new entries.  A trade
        # opened earlier is evaluated against its own exit pair; an entry in the
        # same batch is still allowed below.
        self._evaluate_normal_exits(at)
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

    def _evaluate_normal_exits(self, at: datetime) -> int:
        closed = 0
        for trade in self.repository.open_normal_tracking_trades():
            if trade.entry_signal_date >= at.date():
                continue
            strategy = trade.strategy
            source = self.raw_provider.source_bar(strategy.signal_code, at)
            if source is None:
                continue
            periods = (3, 5, 10, 20, 30, 50, strategy.exit_fast_ma, strategy.exit_slow_ma)
            prior = self.raw_provider.prior_daily_closes(strategy.signal_code, at.date(), max(periods))
            try:
                ma = evaluate_ma(prior_closes=prior, today_1518_close=float(source["close"]), periods=periods)
            except ValueError:
                continue
            if not evaluate_strategy(strategy=strategy, ma=ma).normal_exit:
                continue
            execution = first_actual_execution_bar(
                bars=self.raw_provider.execution_bars(strategy.execution_code, at), signal_time=at)
            if execution is not None and self.repository.record_normal_exit(
                paper_trade_id=trade.paper_trade_id, signal_time=at,
                execution_time=execution.time, execution_price=execution.open_price,
            ):
                closed += 1
        return closed

    def evaluate_day20(self, at: datetime) -> int:
        """Evaluate one completed source minute for currently actual-OPEN trades.

        The caller passes only persisted completed KRX bars.  Existing actual
        normal exits are absent from this set, while a same-day 15:18 DAY20
        trigger remains earlier than the planned 15:19 normal execution bar.
        """
        if not time(9, 0) <= at.time() <= time(15, 30):
            return 0
        closed = 0
        for trade in self.repository.open_day20_trades():
            source = self.raw_provider.completed_source_bar(trade.strategy.signal_code, at)
            prior_close = self.raw_provider.previous_official_close(trade.strategy.signal_code, at)
            if source is None or prior_close is None:
                continue
            if not day20_triggered(direction=trade.strategy.direction, source_close=float(source["close"]),
                                   previous_official_close=prior_close):
                continue
            execution = first_actual_bar_after(
                bars=self.raw_provider.execution_bars_after(trade.strategy.execution_code, at), after=at)
            if execution is not None and self.repository.record_day20_exit(
                paper_trade_id=trade.paper_trade_id, trigger_time=at,
                execution_time=execution.time, execution_price=execution.open_price,
            ):
                closed += 1
        return closed
