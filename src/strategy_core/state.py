"""Explicit serializable strategy state.  No global or singleton state is used."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass
class S1State:
    or_high: float | None = None
    or_low: float | None = None
    breakout_time: datetime | None = None
    pullback_time: datetime | None = None
    pullback_low: float | None = None
    entry_time: datetime | None = None

    def serialize(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass
class S2State:
    or_high: float | None = None
    or_low: float | None = None
    failed_breakout_time: datetime | None = None
    vwap: float | None = None
    entry_time: datetime | None = None

    def serialize(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass
class S3State:
    climax_time: datetime | None = None
    climax_high: float | None = None
    climax_open: float | None = None
    climax_close: float | None = None
    entry_signal_time: datetime | None = None
    entry_time: datetime | None = None

    def serialize(self) -> dict[str, Any]:
        return _serialize(asdict(self))


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value
