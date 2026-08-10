"""Read-only intraday daily observation built from already stored RAW bars.

This module intentionally produces no research rows, signals, cycles, or
orders.  It is a display-time observation only; official daily research runs
continue to use ``DAILY_COMPLETE`` data exclusively.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from src.analysis.feature.sma_feature import MinuteBar
from src.service.research_complete_replay_service import ACCUMULATED, SINGLE, DailyCompleteReplay


@dataclass(frozen=True)
class RawMinute:
    at: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: Decimal


def _decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _strategy_signal_types(strategy_code: str) -> set[str]:
    if strategy_code == "ALL":
        return {"SIGNAL_1", "SIGNAL_2", "SIGNAL_3"}
    return SINGLE.get(strategy_code) or ACCUMULATED.get(strategy_code) or set()


def _has_unallowed_gap(rows: list[RawMinute]) -> bool:
    """Recognize only intra-session minute gaps; auction/session gaps are valid."""
    from src.service.research_complete_replay_service import _session_id
    for previous, current in zip(rows, rows[1:]):
        previous_session, current_session = _session_id(previous.at), _session_id(current.at)
        if previous_session is not None and previous_session == current_session and current.at - previous.at > timedelta(minutes=1):
            return True
    return False


def _daily_bar(at: datetime, opening, high, low, close) -> MinuteBar:
    return MinuteBar(at, _decimal(opening), _decimal(high), _decimal(low), _decimal(close))


def observe(*, stock_code: str, daily_history: Iterable[MinuteBar], minute_rows: Iterable[RawMinute],
            official_today: MinuteBar | None, period: int, strategy_code: str,
            entry_condition: str, direction: str = "ALL") -> dict:
    """Return one transient daily observation without persisting anything."""
    if period <= 0:
        raise ValueError("period must be positive")
    history = sorted(daily_history, key=lambda item: item.bar_time)
    minutes = sorted(minute_rows, key=lambda item: item.at)
    effective = official_today
    status = "DAILY_COMPLETE" if official_today is not None else "INTRADAY_DATA_MISSING"
    volume = None
    latest = None
    gap = False
    if effective is None and minutes:
        latest = minutes[-1].at
        effective = _daily_bar(latest, minutes[0].open_price,
                               max(item.high_price for item in minutes),
                               min(item.low_price for item in minutes), minutes[-1].close_price)
        volume = sum((item.volume for item in minutes), Decimal("0"))
        gap = _has_unallowed_gap(minutes)
        status = "DATA_GAP" if gap else "PROVISIONAL_DAILY"
    elif official_today is not None:
        latest = official_today.bar_time

    result = {
        "stock_code": stock_code,
        "latest_minute_time": latest,
        "status": status,
        "open_price": None, "high_price": None, "low_price": None, "close_price": None,
        "volume": volume, "ma": None, "ma_direction": None,
        "canonical_signals": [], "entry_condition": entry_condition,
        "condition_satisfied": False, "strategy_code": strategy_code,
    }
    if effective is None:
        return result
    result.update({"open_price": effective.open_price, "high_price": effective.high_price,
                   "low_price": effective.low_price, "close_price": effective.close_price})
    if official_today is not None:
        # Daily RAW volume is not part of the feature object; callers may add it.
        result["volume"] = result["volume"]

    closes = [item.close_price for item in history]
    if len(closes) >= period:
        previous_ma = sum(closes[-period:]) / Decimal(period)
        current_ma = (sum(closes[-(period - 1):]) + effective.close_price) / Decimal(period) if period > 1 else effective.close_price
        result["ma"] = current_ma
        result["ma_direction"] = "UP" if current_ma > previous_ma else "DOWN" if current_ma < previous_ma else "FLAT"

    # Canonical calculation is shared with official daily replay, but is kept
    # strictly in memory and is never written to research_* tables.
    replay = DailyCompleteReplay(entry_condition=entry_condition, confirm_period=period)
    features = replay.features([*history, effective])
    signals = replay.canonical_signals(features)
    now_signals = [item for item in signals if item.at == effective.bar_time and item.signal_type in _strategy_signal_types(strategy_code)]
    if direction != "ALL":
        now_signals = [item for item in now_signals if item.direction == direction]
    result["canonical_signals"] = [{"signal_type": item.signal_type, "direction": item.direction} for item in now_signals]
    if status == "DATA_GAP":
        return result
    if now_signals:
        if entry_condition == DailyCompleteReplay.SIGNAL_ONLY:
            result["condition_satisfied"] = True
        else:
            result["condition_satisfied"] = any(result["ma_direction"] == item.direction for item in now_signals)
    return result
