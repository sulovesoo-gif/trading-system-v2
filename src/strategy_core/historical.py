"""Read-only historical adapter for the public Strategy Core decision API.

This module owns no database connection and knows nothing about Golden fixtures.
Callers supply completed RAW bars; the adapter maps Core target timestamps to
actual execution-product bars and builds comparable historical records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from .bars import CompletedBar
from .contracts import SignalDecision


@dataclass(frozen=True)
class HistoricalTrade:
    strategy_instance: str
    strategy_code: str
    trade_date: str
    signal_stock_code: str
    signal_direction: str
    execution_stock_code: str
    execution_direction: str
    signal_time: datetime
    entry_target_time: datetime
    entry_execution_time: datetime
    exit_trigger_time: datetime
    exit_execution_time: datetime
    raw_entry_price: float
    raw_exit_price: float
    exit_reason: str
    shared_entry_group: str | None
    reference_levels: Mapping[str, object]

    def golden_key(self) -> tuple[str, str, str, str, str, str]:
        return (
            self.strategy_instance, self.trade_date, self.signal_time.isoformat(),
            self.entry_execution_time.isoformat(), self.exit_execution_time.isoformat(),
            self.exit_reason,
        )


class HistoricalDataProvider:
    """In-memory completed bar source used identically by replay and validation."""

    def __init__(self, bars_by_stock: Mapping[str, Iterable[CompletedBar]]) -> None:
        self._bars = {
            stock: tuple(sorted(values, key=lambda value: value.time))
            for stock, values in bars_by_stock.items()
        }

    def bars(self, stock_code: str, trading_date: str) -> tuple[CompletedBar, ...]:
        return tuple(bar for bar in self._bars.get(stock_code, ()) if bar.time.date().isoformat() == trading_date)

    def bar_at(self, stock_code: str, at: datetime) -> CompletedBar | None:
        return next((bar for bar in self._bars.get(stock_code, ()) if bar.time == at), None)

    def next_bar_after(self, stock_code: str, at: datetime) -> CompletedBar | None:
        return next((bar for bar in self._bars.get(stock_code, ()) if bar.time > at), None)


class HistoricalExecutionAdapter:
    """Maps price-free Core decisions to actual historical execution bars."""

    def __init__(self, provider: HistoricalDataProvider) -> None:
        self.provider = provider

    def entry_bar(self, decision: SignalDecision) -> CompletedBar:
        if decision.target_time is None:
            raise ValueError("entry decision requires target_time")
        bar = self.provider.bar_at(decision.execution_stock_code, decision.target_time)
        if bar is None:
            raise LookupError(f"missing exact entry execution bar: {decision.execution_stock_code} {decision.target_time}")
        return bar

    def exit_bar(self, decision: SignalDecision, *, eod_uses_close: bool = False) -> CompletedBar:
        if decision.target_time is None:
            raise ValueError("exit decision requires target_time")
        bar = self.provider.bar_at(decision.execution_stock_code, decision.target_time)
        if bar is None:
            raise LookupError(f"missing exact exit execution bar: {decision.execution_stock_code} {decision.target_time}")
        return bar

    @staticmethod
    def entry_price(bar: CompletedBar) -> float:
        return bar.open

    @staticmethod
    def exit_price(bar: CompletedBar, *, eod_uses_close: bool) -> float:
        return bar.close if eod_uses_close else bar.open
