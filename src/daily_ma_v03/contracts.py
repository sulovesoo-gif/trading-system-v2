"""Pure contracts for the approved Daily MA V0.3 PAPER runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Mapping


class ActualExitKind(str, Enum):
    DAY20 = "DAY20"
    NORMAL = "NORMAL"


@dataclass(frozen=True)
class SignalEvent:
    signal_code: str
    direction: str
    entry_fast_ma: int
    entry_slow_ma: int
    signal_date: str
    source_bar_time: datetime
    venue: str = "KRX"

    def key(self) -> str:
        return (
            f"DAILY_MA_V03|{self.signal_code}|{self.direction}|"
            f"MA{self.entry_fast_ma}_MA{self.entry_slow_ma}|{self.signal_date}|"
            f"{self.source_bar_time:%Y-%m-%dT%H:%M:%S}|{self.venue}"
        )


@dataclass(frozen=True)
class ExecutionBar:
    time: datetime
    open_price: float


def choose_actual_exit(*, day20_trigger_time: datetime | None,
                       normal_exit_execution_time: datetime) -> ActualExitKind:
    """Historical replay contract: DAY20 wins only when strictly earlier."""
    if day20_trigger_time is not None and day20_trigger_time < normal_exit_execution_time:
        return ActualExitKind.DAY20
    return ActualExitKind.NORMAL


def snapshot_payload(*, source_bar: Mapping[str, object], prior_close_hash: str,
                     entry_fast_value: float, entry_slow_value: float,
                     trend_value: float | None, trend_passed: bool,
                     direction: str, venue: str, data_source: str) -> dict[str, object]:
    """Identity-independent input evidence for replay mismatch detection."""
    return {
        "source_bar": dict(source_bar),
        "prior_daily_close_hash": prior_close_hash,
        "entry_fast_ma": entry_fast_value,
        "entry_slow_ma": entry_slow_value,
        "trend_ma": trend_value,
        "trend_passed": trend_passed,
        "direction": direction,
        "venue": venue,
        "data_source": data_source,
    }
