"""Policy-aware V1 PAPER/NO_SEND orchestration.

The repository owns durability.  The runtime intentionally has no broker POST
capability and never invokes the legacy EOD close path.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .engine import MinuteMaSignalEngine, SignalType
from .v1_policy import paper_stop_execution_time


@dataclass(frozen=True)
class V1RuntimeResult:
    paths_evaluated: int
    entries_created: int
    normal_exits: int
    stop_exits: int
    rejected_entries: int


class MinuteMaV1PaperRuntime:
    """Run KRX_CONTINUOUS V1 paths with HOLD and trade-specific stops."""

    def __init__(self, repository, *, engine=None) -> None:
        self.repository = repository
        self.engine = engine or MinuteMaSignalEngine()

    def run_day(self, *, trading_date: date) -> V1RuntimeResult:
        paths = self.repository.v1_policy_paths()
        grouped = defaultdict(list)
        for path in paths:
            grouped[path.signal_code].append(path)
        created = normal = stopped = rejected = 0
        for signal_code, group in grouped.items():
            bars = self.repository.source_bars(
                stock_code=signal_code, axis=group[0].axis, trading_date=trading_date)
            points = self.engine.prepare(path=group[0], bars=bars)
            cursor = self.repository.v1_runtime_cursor(signal_code=signal_code)
            if cursor is not None:
                points = tuple(point for point in points if point.bar_time > cursor)
            events_by_time = defaultdict(list)
            for path in group:
                for event in self.engine.evaluate_prepared(path=path, points=points):
                    if event.source_bar_time.date() == trading_date:
                        events_by_time[event.source_bar_time].append((path,event))
            for point in points:
                # A completed underlying close can stop each independently owned trade.
                stopped += self._apply_stops(group, point)
                events = sorted(events_by_time.get(point.bar_time,()),
                                key=lambda item: 0 if item[1].signal_type is SignalType.EXIT else 1)
                for path,event in events:
                    policy = path.operation_policy
                    if event.signal_type is SignalType.ENTRY:
                        if not policy.allows_entry(event.source_bar_time, live=False):
                            rejected += 1
                            continue
                        proxy_time = event.source_bar_time + timedelta(minutes=1)
                        execution_bar = self.repository.execution_bar(
                            stock_code=path.execution_code, at=proxy_time)
                        underlying_bar = self.repository.underlying_bar(
                            stock_code=path.signal_code, at=proxy_time)
                        if execution_bar is None or underlying_bar is None:
                            rejected += 1
                            continue
                        created += self.repository.v1_open_trade(
                            path=path, event=event, execution_bar=execution_bar,
                            underlying_entry_reference_price=Decimal(str(underlying_bar.open_price)))
                    else:
                        proxy_time = event.source_bar_time + timedelta(minutes=1)
                        execution_bar = self.repository.execution_bar(
                            stock_code=path.execution_code, at=proxy_time)
                        if execution_bar is not None:
                            normal += self.repository.v1_close_normal(
                                path=path, event=event, execution_bar=execution_bar)
            if points:
                self.repository.advance_v1_cursor(
                    signal_code=signal_code, last_source_bar_time=points[-1].bar_time)
        return V1RuntimeResult(len(paths), created, normal, stopped, rejected)

    def _apply_stops(self, group, point) -> int:
        count = 0
        close = Decimal(str(point.source_close))
        for path in group:
            for trade in self.repository.v1_open_trades(path=path):
                if trade.entry_execution_time > point.bar_time:
                    continue
                if not path.operation_policy.stop_triggered(
                        anchor=trade.underlying_entry_reference_price,
                        completed_underlying_close=close):
                    continue
                execution_bar = self.repository.execution_bar(
                    stock_code=path.execution_code,
                    at=paper_stop_execution_time(point.bar_time))
                if execution_bar is not None:
                    count += self.repository.v1_close_stop(
                        path=path, trade=trade, trigger_bar_time=point.bar_time,
                        trigger_underlying_close=close, execution_bar=execution_bar)
        return count
