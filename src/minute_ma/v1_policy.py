"""Frozen Minute-MA V1.0 operating policy.

Only the session/holding/underlying-stop overlay lives here.  MA crossover
semantics remain in :mod:`src.minute_ma.engine` and the eight legacy research
axes remain unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from hashlib import sha256


class StopDirection(str, Enum):
    ABOVE = "ABOVE"
    BELOW = "BELOW"


@dataclass(frozen=True)
class MinuteMaV1Policy:
    policy_code: str
    direction: str
    paper_entry_start: time
    paper_entry_end: time
    live_entry_start: time
    live_entry_end: time
    stop_percent: Decimal
    stop_direction: StopDirection
    holding_policy: str = "HOLD_TO_NORMAL_EXIT_OR_STOP"
    policy_version: str = "MINUTE_MA_OPERATION_V1.0"

    def __post_init__(self) -> None:
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")
        if self.stop_percent <= 0:
            raise ValueError("stop percent must be positive")
        if self.paper_entry_end < self.paper_entry_start or self.live_entry_end < self.live_entry_start:
            raise ValueError("invalid entry window")

    def allows_entry(self, source_bar_time: datetime, *, live: bool) -> bool:
        start, end = ((self.live_entry_start, self.live_entry_end)
                      if live else (self.paper_entry_start, self.paper_entry_end))
        return start <= source_bar_time.time() <= end

    def threshold(self, anchor: Decimal) -> Decimal:
        ratio = self.stop_percent / Decimal("100")
        return anchor * (Decimal("1") + ratio if self.stop_direction is StopDirection.ABOVE
                         else Decimal("1") - ratio)

    def stop_triggered(self, *, anchor: Decimal, completed_underlying_close: Decimal) -> bool:
        threshold = self.threshold(anchor)
        return (completed_underlying_close >= threshold
                if self.stop_direction is StopDirection.ABOVE
                else completed_underlying_close <= threshold)

    def adverse_percent(self, *, anchor: Decimal, current: Decimal) -> Decimal:
        move = (current / anchor - Decimal("1")) * Decimal("100")
        return move if self.stop_direction is StopDirection.ABOVE else -move


SHORT_POLICY = MinuteMaV1Policy(
    policy_code="MINUTE_MA_V1_SHORT",
    direction="SHORT",
    paper_entry_start=time(9, 0), paper_entry_end=time(9, 59),
    live_entry_start=time(9, 0), live_entry_end=time(9, 29),
    stop_percent=Decimal("1"), stop_direction=StopDirection.ABOVE,
)

LONG_POLICY = MinuteMaV1Policy(
    policy_code="MINUTE_MA_V1_LONG",
    direction="LONG",
    paper_entry_start=time(14, 0), paper_entry_end=time(15, 18),
    live_entry_start=time(15, 0), live_entry_end=time(15, 18),
    stop_percent=Decimal("5"), stop_direction=StopDirection.BELOW,
)


def policy_for_direction(direction: str) -> MinuteMaV1Policy:
    if direction == "LONG":
        return LONG_POLICY
    if direction == "SHORT":
        return SHORT_POLICY
    raise ValueError(f"unsupported direction: {direction}")


def stop_event_key(*, policy_path_id: int, trade_id: int, trigger_bar_time: datetime) -> str:
    material = f"MINUTE_MA_V1|STOP|{policy_path_id}|{trade_id}|{trigger_bar_time.isoformat()}"
    return sha256(material.encode("utf-8")).hexdigest()


def paper_stop_execution_time(trigger_bar_time: datetime) -> datetime:
    """Validation SQL contract: first actual execution bar strictly after trigger."""
    return trigger_bar_time + timedelta(minutes=1)
