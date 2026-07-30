"""Completed-minute SMA5/SMA10 cross and close-cross events."""

from __future__ import annotations

from dataclasses import dataclass

from src.analysis.feature.sma_feature import SmaFeature


@dataclass(frozen=True)
class CrossSignal:
    direction: str
    candle_direction: str
    direction_alignment: str


def detect_ma_cross(previous: SmaFeature, current: SmaFeature) -> CrossSignal | None:
    """Detect the moving-average cross only; the price cross is a later ARMED step."""
    up = previous.sma5 <= previous.sma10 and current.sma5 > current.sma10
    down = previous.sma5 >= previous.sma10 and current.sma5 < current.sma10
    if not up and not down:
        return None
    direction = "LONG" if up else "SHORT"
    candle_direction = _candle_direction(current.bar.open_price, current.bar.close_price)
    return CrossSignal(direction, candle_direction, _alignment(direction, candle_direction))


def detect_close_cross(previous: SmaFeature, current: SmaFeature, direction: str) -> bool:
    """Detect the post-arm close/SMA10 cross using completed bars only."""
    if direction == "LONG":
        return previous.bar.close_price <= previous.sma10 and current.bar.close_price > current.sma10
    if direction == "SHORT":
        return previous.bar.close_price >= previous.sma10 and current.bar.close_price < current.sma10
    raise ValueError(f"Unsupported armed direction: {direction}")


def detect_cross_signal(previous: SmaFeature, current: SmaFeature) -> CrossSignal | None:
    """Legacy same-bar rule retained only for comparison tests."""
    event = detect_ma_cross(previous, current)
    if event is None or not detect_close_cross(previous, current, event.direction):
        return None
    return event


def _candle_direction(open_price, close_price) -> str:
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
