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

    @property
    def market_source(self) -> MarketSource:
        return MarketSource.INTEGRATED if self.value.startswith("INTEGRATED") else MarketSource.KRX

    @property
    def continuity(self) -> ContinuityMode:
        return ContinuityMode.CONTINUOUS if self.value.endswith("CONTINUOUS") else ContinuityMode.RESET

    @property
    def session(self) -> tuple[time, time]:
        if self.market_source is MarketSource.KRX:
            return time(9, 0), time(15, 30)
        return time(8, 0), time(19, 59)


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

    def __post_init__(self) -> None:
        if self.direction not in {"LONG", "SHORT"}:
            raise ValueError("direction must be LONG or SHORT")
        if not (0 < self.entry_fast_ma < self.entry_slow_ma):
            raise ValueError("invalid entry MA pair")
        if not (0 < self.exit_fast_ma < self.exit_slow_ma):
            raise ValueError("invalid exit MA pair")
        if self.trend_ma is not None and self.trend_ma <= 0:
            raise ValueError("trend MA must be positive")

