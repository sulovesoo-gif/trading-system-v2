"""Execution-bar resolution. Never fabricates a bar or crosses KRX close."""

from __future__ import annotations

from datetime import datetime, time
from typing import Iterable

from .contracts import ExecutionBar


def first_actual_execution_bar(*, bars: Iterable[ExecutionBar], signal_time: datetime) -> ExecutionBar | None:
    """Return first same-day actual KRX bar after signal, constrained to 15:30."""
    candidates = sorted(
        (bar for bar in bars
         if bar.time.date() == signal_time.date()
         and bar.time > signal_time
         and time(15, 19) <= bar.time.time() <= time(15, 30)),
        key=lambda bar: bar.time,
    )
    return candidates[0] if candidates else None
