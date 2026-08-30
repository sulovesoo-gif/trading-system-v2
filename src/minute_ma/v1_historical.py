"""Deterministic V1.0 Historical replay, isolated from Forward PAPER ledgers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Mapping

from .contracts import MinuteBar, MinuteMaPath
from .engine import MinuteMaSignalEngine, SignalType
from .v1_policy import paper_stop_execution_time

INITIAL_VIRTUAL_CAPITAL = Decimal("1000000")
ROUND_TRIP_COST_PCT = Decimal("0.20")


@dataclass(frozen=True)
class V1HistoricalTrade:
    entry_event_key: str
    entry_signal_time: datetime
    entry_execution_time: datetime
    entry_price: Decimal
    underlying_entry_reference_price: Decimal
    stop_threshold_price: Decimal
    exit_signal_time: datetime
    exit_execution_time: datetime
    exit_price: Decimal
    exit_reason: str
    stop_trigger_time: datetime | None
    stop_trigger_underlying_close: Decimal | None
    basis_capital: Decimal
    gross_return_pct: Decimal
    net_return_pct: Decimal
    realized_pnl: Decimal


@dataclass
class _Open:
    event_key: str
    signal_time: datetime
    execution_time: datetime
    price: Decimal
    anchor: Decimal
    threshold: Decimal
    basis: Decimal


class MinuteMaV1HistoricalReplay:
    """Replay the frozen V1 policy without writing Forward PAPER tables."""

    def __init__(self, *, engine: MinuteMaSignalEngine | None = None) -> None:
        self.engine = engine or MinuteMaSignalEngine()

    def replay(self, *, path: MinuteMaPath, prepared_points,
               execution_bars: Mapping[datetime, MinuteBar],
               underlying_bars: Mapping[datetime, MinuteBar],
               evaluation_from: date, evaluation_to: date) -> tuple[V1HistoricalTrade, ...]:
        events = self.engine.evaluate_prepared(path=path, points=prepared_points)
        by_time: dict[datetime, list] = {}
        for event in events:
            if evaluation_from <= event.source_bar_time.date() <= evaluation_to:
                by_time.setdefault(event.source_bar_time, []).append(event)
        capital = INITIAL_VIRTUAL_CAPITAL
        opened: list[_Open] = []
        completed: list[V1HistoricalTrade] = []

        def close(trade: _Open, *, signal_time: datetime, execution: MinuteBar,
                  reason: str, trigger_time=None, trigger_close=None) -> None:
            nonlocal capital
            exit_price = Decimal(str(execution.open_price))
            gross = (exit_price / trade.price - Decimal("1")) * Decimal("100")
            net = gross - ROUND_TRIP_COST_PCT
            pnl = trade.basis * net / Decimal("100")
            capital += pnl
            completed.append(V1HistoricalTrade(
                trade.event_key, trade.signal_time, trade.execution_time, trade.price,
                trade.anchor, trade.threshold, signal_time, execution.bar_time, exit_price,
                reason, trigger_time, trigger_close, trade.basis, gross, net, pnl,
            ))

        for point in prepared_points:
            if not evaluation_from <= point.bar_time.date() <= evaluation_to:
                continue
            point_close = Decimal(str(point.source_close))
            # Production V1 applies trade-specific STOP before same-bar MA events.
            for trade in tuple(opened):
                if trade.execution_time > point.bar_time:
                    continue
                if not path.operation_policy.stop_triggered(
                        anchor=trade.anchor, completed_underlying_close=point_close):
                    continue
                execution = execution_bars.get(paper_stop_execution_time(point.bar_time))
                if execution is None:
                    continue
                opened.remove(trade)
                close(trade, signal_time=point.bar_time + timedelta(minutes=1, seconds=1),
                      execution=execution, reason="STOP_EXIT",
                      trigger_time=point.bar_time, trigger_close=point_close)

            events_at = sorted(by_time.get(point.bar_time, ()),
                               key=lambda e: 0 if e.signal_type is SignalType.EXIT else 1)
            for event in events_at:
                proxy_time = event.source_bar_time + timedelta(minutes=1)
                execution = execution_bars.get(proxy_time)
                if execution is None:
                    continue
                if event.signal_type is SignalType.EXIT:
                    for trade in tuple(opened):
                        opened.remove(trade)
                        close(trade, signal_time=event.confirmed_at, execution=execution,
                              reason="NORMAL_EXIT")
                    continue
                if not path.operation_policy.allows_entry(event.source_bar_time, live=False):
                    continue
                underlying = underlying_bars.get(proxy_time)
                if underlying is None:
                    continue
                anchor = Decimal(str(underlying.open_price))
                opened.append(_Open(
                    event.signal_event_key, event.confirmed_at, proxy_time,
                    Decimal(str(execution.open_price)), anchor,
                    path.operation_policy.threshold(anchor), capital,
                ))
        return tuple(completed)
