from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum


class MarketSource(str, Enum):
    KRX = "KRX"
    INTEGRATED = "INTEGRATED"


class ContinuityMode(str, Enum):
    CONTINUOUS = "CONTINUOUS"
    RESET = "RESET"


class Axis(str, Enum):
    KRX_CONTINUOUS = "KRX_CONTINUOUS"
    KRX_RESET = "KRX_RESET"
    INTEGRATED_CONTINUOUS = "INTEGRATED_CONTINUOUS"
    INTEGRATED_RESET = "INTEGRATED_RESET"
    KRX_CONTINUOUS_AFTERNOON = "KRX_CONTINUOUS_AFTERNOON"
    KRX_RESET_AFTERNOON = "KRX_RESET_AFTERNOON"
    INTEGRATED_CONTINUOUS_AFTERNOON = "INTEGRATED_CONTINUOUS_AFTERNOON"
    INTEGRATED_RESET_AFTERNOON = "INTEGRATED_RESET_AFTERNOON"

    @property
    def is_afternoon(self) -> bool:
        return self.value.endswith("_AFTERNOON")

    @property
    def base_value(self) -> str:
        return self.value.removesuffix("_AFTERNOON")

    @property
    def market_source(self) -> MarketSource:
        return MarketSource.INTEGRATED if self.base_value.startswith("INTEGRATED") else MarketSource.KRX

    @property
    def continuity(self) -> ContinuityMode:
        return (ContinuityMode.CONTINUOUS
                if self.base_value.endswith("CONTINUOUS")
                else ContinuityMode.RESET)

    @property
    def session(self) -> tuple[time, time]:
        if self.market_source is MarketSource.KRX:
            return time(9, 0), time(15, 30)
        return time(8, 0), time(19, 59)

    @property
    def entry_source_session(self) -> tuple[time, time]:
        """KRX-executable source-bar window for a new ENTRY event."""
        start = time(14, 0) if self.is_afternoon else time(9, 0)
        if self.continuity is ContinuityMode.RESET:
            return start, time(14, 59)
        return start, time(15, 18)

    def allows_entry_source_time(self, value: time) -> bool:
        start, end = self.entry_source_session
        return start <= value <= end


@dataclass(frozen=True)
class MinuteBar:
    bar_time: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: int = 0

    def __post_init__(self) -> None:
        if min(self.open_price, self.high_price, self.low_price, self.close_price) <= 0:
            raise ValueError("minute OHLC must be positive")


@dataclass(frozen=True)
class MinuteMaPath:
    minute_path_id: int
    path_key: str
    axis: Axis
    signal_code: str
    execution_code: str
    direction: str
    entry_fast_ma: int
    entry_slow_ma: int
    exit_fast_ma: int
    exit_slow_ma: int
    trend_ma: int | None
    source_daily_strategy_id: str | None = None
    minute_policy_path_id: int | None = None
    operation_policy: object | None = None

    def __post_init__(self) -> None:
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")
        if not (0 < self.entry_fast_ma < self.entry_slow_ma):
            raise ValueError("invalid entry MA pair")
        if not (0 < self.exit_fast_ma < self.exit_slow_ma):
            raise ValueError("invalid exit MA pair")
        if self.trend_ma is not None and self.trend_ma <= 0:
            raise ValueError("trend MA must be positive")
