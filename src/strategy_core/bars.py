"""Completed-bar inputs only.  Snapshot and execution prices are deliberately absent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CompletedBar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    accumulated_amount: float | None = None


@dataclass(frozen=True)
class S1Evidence:
    bar: CompletedBar
    or_high: float
    or_low: float
    breakout: bool
    pullback: bool
    restart: bool
    pullback_low: float | None


@dataclass(frozen=True)
class S2Evidence:
    bar: CompletedBar
    or_high: float
    or_low: float
    vwap: float | None
    failed_breakout: bool
    short_signal: bool
