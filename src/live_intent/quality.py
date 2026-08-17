"""Generic, conservative data-quality policy for completed-bar strategy inputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from src.strategy_core.bars import CompletedBar


@dataclass(frozen=True)
class DataQualityResult:
    status: str
    reason: str
    detail: dict[str, object] = field(default_factory=dict)

    @property
    def entry_allowed(self) -> bool:
        return self.status in {"PASS", "LEGITIMATE_NO_BAR"}


@dataclass(frozen=True)
class MarketContext:
    """Verified market-state information supplied by an outer market-data layer.

    The gate never guesses that a clock gap is normal.  A caller may explicitly
    provide verified no-bar intervals (CB/VI/halt/no-trade) when such evidence is
    available.
    """

    legitimate_no_bar_intervals: tuple[tuple[datetime, datetime, str], ...] = ()
    max_staleness: timedelta = timedelta(minutes=2)


class DataQualityGate:
    def evaluate_entry(self, *, required_lookback: int, evaluated_at: datetime,
                       completed_bars: Iterable[CompletedBar], context: MarketContext = MarketContext()) -> DataQualityResult:
        bars = tuple(sorted((bar for bar in completed_bars if bar.time <= evaluated_at), key=lambda value: value.time))
        if len(bars) < required_lookback:
            return DataQualityResult("BLOCKED_INSUFFICIENT_HISTORY", "required completed-bar history unavailable", {"required": required_lookback, "actual": len(bars)})
        last = bars[-1]
        if evaluated_at - last.time > context.max_staleness:
            return DataQualityResult("BLOCKED_STALE_DATA", "latest completed bar is stale", {"last_bar": last.time.isoformat(), "evaluated_at": evaluated_at.isoformat()})
        window = bars[-required_lookback:]
        legitimate = False
        for before, after in zip(window, window[1:]):
            if after.time - before.time <= timedelta(minutes=1):
                continue
            matching = next((kind for start, end, kind in context.legitimate_no_bar_intervals if before.time >= start and after.time <= end), None)
            if matching:
                legitimate = True
                continue
            return DataQualityResult("BLOCKED_DATA_GAP", "unverified completed-bar gap", {"before": before.time.isoformat(), "after": after.time.isoformat(), "required_lookback": required_lookback})
        return DataQualityResult("LEGITIMATE_NO_BAR" if legitimate else "PASS", "verified no-bar interval" if legitimate else "contiguous completed-bar history", {"last_bar": last.time.isoformat()})

    def evaluate_exit(self, *, required_lookback: int, evaluated_at: datetime,
                      completed_bars: Iterable[CompletedBar], context: MarketContext = MarketContext()) -> DataQualityResult:
        result = self.evaluate_entry(required_lookback=required_lookback, evaluated_at=evaluated_at, completed_bars=completed_bars, context=context)
        if result.entry_allowed:
            return result
        return DataQualityResult("EXIT_DATA_UNCERTAIN", result.reason, result.detail)
