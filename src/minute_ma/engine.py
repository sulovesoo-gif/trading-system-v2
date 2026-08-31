from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
from typing import Iterable

from src.ma_crossover import GapTransition, classify_gap_transition

from .contracts import ContinuityMode, MinuteBar, MinuteMaPath


class SignalType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


@dataclass(frozen=True)
class SignalEvent:
    minute_path_id: int
    path_key: str
    signal_type: SignalType
    source_bar_time: datetime
    confirmed_at: datetime
    signal_event_key: str
    trend_passed: bool
    ma_values: dict[int, float]
    previous_ma_values: dict[int, float]
    signal_source: str = "REST_1MIN_LEGACY"


@dataclass(frozen=True)
class PreparedMaPoint:
    bar_time: datetime
    current: dict[int,float]
    previous: dict[int,float] | None
    source_close: float = 0.0
    finalized_at: datetime | None = None
    source_name: str = "REST_1MIN_LEGACY"


class _RollingMa:
    def __init__(self, periods: Iterable[int]) -> None:
        self.periods = tuple(sorted(set(periods)))
        self.values: deque[float] = deque(maxlen=max(self.periods))

    def reset(self) -> None:
        self.values.clear()

    def push(self, value: float) -> dict[int, float] | None:
        self.values.append(value)
        seq = tuple(self.values)
        values={period:sum(seq[-period:])/period for period in self.periods if len(seq)>=period}
        return values or None


def _event_key(path: MinuteMaPath, signal_type: SignalType, source_bar_time: datetime) -> str:
    material = f"MINUTE_MA_V01|{path.path_key}|{signal_type.value}|{source_bar_time.isoformat()}"
    return sha256(material.encode("utf-8")).hexdigest()


class MinuteMaSignalEngine:
    """One MA engine; venue and continuity are path parameters.

    Events are transition-only crossovers. A held condition cannot emit on
    subsequent bars, and deterministic keys make replay/restart idempotent.
    """

    ALL_PERIODS=(3,5,10,20,30,50)

    def prepare(self,*,path:MinuteMaPath,bars:Iterable[MinuteBar]) -> tuple[PreparedMaPoint,...]:
        """Calculate one source/axis MA stream for all 2,400 semantic paths."""
        rolling = _RollingMa(self.ALL_PERIODS)
        prior_values: dict[int, float] | None = None
        prior_date = None
        points=[]
        start, end = path.axis.session
        ordered = sorted(bars, key=lambda bar: bar.bar_time)
        seen: set[datetime] = set()
        for bar in ordered:
            if bar.bar_time in seen:
                continue
            seen.add(bar.bar_time)
            if not start <= bar.bar_time.time() <= end:
                continue
            if not bar.signal_eligible:
                # Do not bridge a crossover across a rejected realtime source bar.
                prior_values = None
                continue
            if (path.axis.continuity is ContinuityMode.RESET
                    and prior_date is not None and bar.bar_time.date() != prior_date):
                rolling.reset()
                prior_values = None
            prior_date = bar.bar_time.date()
            current = rolling.push(bar.close_price)
            if current is None:
                continue
            points.append(PreparedMaPoint(
                bar.bar_time,current,prior_values,bar.close_price,bar.finalized_at,bar.source_name))
            prior_values=current
        return tuple(points)

    def evaluate_prepared(self,*,path:MinuteMaPath,
                          points:Iterable[PreparedMaPoint]) -> tuple[SignalEvent,...]:
        events: list[SignalEvent] = []
        required={path.entry_fast_ma,path.entry_slow_ma,path.exit_fast_ma,path.exit_slow_ma}
        if path.trend_ma is not None: required.add(path.trend_ma)
        for point in points:
            current,prior_values=point.current,point.previous
            if prior_values is None or not required.issubset(current) or not required.issubset(prior_values):
                continue

            direction = path.direction
            entry_transition = classify_gap_transition(
                previous_gap=prior_values[path.entry_fast_ma] - prior_values[path.entry_slow_ma],
                current_gap=current[path.entry_fast_ma] - current[path.entry_slow_ma],
            )
            exit_transition = classify_gap_transition(
                previous_gap=prior_values[path.exit_fast_ma] - prior_values[path.exit_slow_ma],
                current_gap=current[path.exit_fast_ma] - current[path.exit_slow_ma],
            )
            trend_passed = True
            if path.trend_ma is not None:
                trend_passed = (current[path.trend_ma] > prior_values[path.trend_ma]
                                if direction == "LONG"
                                else current[path.trend_ma] < prior_values[path.trend_ma])
            entry_cross = GapTransition.UP_CROSS if direction == "LONG" else GapTransition.DOWN_CROSS
            exit_cross = GapTransition.DOWN_CROSS if direction == "LONG" else GapTransition.UP_CROSS
            entry = entry_transition is entry_cross and trend_passed
            exit_ = exit_transition is exit_cross
            confirmed_at = point.finalized_at or point.bar_time + timedelta(minutes=1, seconds=1)
            for kind, emitted in ((SignalType.ENTRY, entry), (SignalType.EXIT, exit_)):
                if emitted:
                    events.append(SignalEvent(
                        path.minute_path_id, path.path_key, kind, point.bar_time,
                        confirmed_at, _event_key(path, kind, point.bar_time),
                        trend_passed, current.copy(), prior_values.copy(),point.source_name,
                    ))
        return tuple(events)

    def evaluate(self, *, path: MinuteMaPath, bars: Iterable[MinuteBar]) -> tuple[SignalEvent, ...]:
        return self.evaluate_prepared(path=path,points=self.prepare(path=path,bars=bars))
