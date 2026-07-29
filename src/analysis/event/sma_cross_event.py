"""SMA5/SMA10 및 종가 돌파 이벤트 판정."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.analysis.feature.sma_feature import SmaFeature


@dataclass(frozen=True)
class CrossSignal:
    direction: str
    candle_direction: str
    direction_alignment: str


def detect_cross_signal(previous: SmaFeature, current: SmaFeature) -> CrossSignal | None:
    up = (
        previous.sma5 <= previous.sma10
        and current.sma5 > current.sma10
        and previous.bar.close_price <= previous.sma10
        and current.bar.close_price > current.sma10
    )
    down = (
        previous.sma5 >= previous.sma10
        and current.sma5 < current.sma10
        and previous.bar.close_price >= previous.sma10
        and current.bar.close_price < current.sma10
    )
    if not up and not down:
        return None
    direction = "LONG" if up else "SHORT"
    candle_direction = _candle_direction(current.bar.open_price, current.bar.close_price)
    return CrossSignal(direction, candle_direction, _alignment(direction, candle_direction))


def threshold_break(close_price: Decimal, reference_price: Decimal) -> tuple[str | None, Decimal]:
    """완료 봉 종가만 사용해 양방향 1% 경계 돌파를 판정한다."""
    change = close_price / reference_price - Decimal("1")
    if change >= Decimal("0.01"):
        return "UP", change
    if change <= Decimal("-0.01"):
        return "DOWN", change
    return None, change


def _candle_direction(open_price: Decimal, close_price: Decimal) -> str:
    if close_price > open_price:
        return "UP"
    if close_price < open_price:
        return "DOWN"
    return "FLAT"


def _alignment(direction: str, candle_direction: str) -> str:
    if candle_direction == "FLAT":
        return "NEUTRAL"
    if (direction == "LONG" and candle_direction == "UP") or (direction == "SHORT" and candle_direction == "DOWN"):
        return "ALIGNED"
    return "OPPOSED"
