"""Frozen Daily MA V0.3 signal calculations used by the PAPER lane only.

This module deliberately contains no broker or database operation.  It mirrors
the historical replay: previous completed daily closes plus the source stock's
15:18 completed minute close form the current-day moving averages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Iterable, Sequence


@dataclass(frozen=True)
class DailyMaStrategy:
    strategy_id: str
    signal_code: str
    execution_code: str
    direction: str
    entry_fast_ma: int
    entry_slow_ma: int
    exit_fast_ma: int
    exit_slow_ma: int
    trend_ma: int | None
    day20_enabled: bool


@dataclass(frozen=True)
class MaEvaluation:
    values_now: dict[int, float]
    values_previous: dict[int, float]
    prior_close_hash: str

    def crossed_up(self, fast: int, slow: int) -> bool:
        return self.values_previous[fast] <= self.values_previous[slow] and self.values_now[fast] > self.values_now[slow]

    def crossed_down(self, fast: int, slow: int) -> bool:
        return self.values_previous[fast] >= self.values_previous[slow] and self.values_now[fast] < self.values_now[slow]


@dataclass(frozen=True)
class StrategyDecision:
    entry: bool
    normal_exit: bool
    trend_passed: bool


def evaluate_ma(*, prior_closes: Sequence[float], today_1518_close: float,
                periods: Iterable[int] = (3, 5, 10, 20, 30, 50)) -> MaEvaluation:
    """Calculate V0.3 current and previous daily MAs without invented prices."""
    if today_1518_close <= 0:
        raise ValueError("today_1518_close must be positive")
    periods = tuple(sorted(set(periods)))
    if not periods or min(periods) <= 0:
        raise ValueError("periods must be positive")
    if len(prior_closes) < max(periods):
        raise ValueError("insufficient prior completed KRX daily closes")
    if any(value <= 0 for value in prior_closes):
        raise ValueError("prior daily closes must be positive")

    previous = {period: sum(prior_closes[-period:]) / period for period in periods}
    with_today = (*prior_closes, today_1518_close)
    current = {period: sum(with_today[-period:]) / period for period in periods}
    serialized = "|".join(f"{value:.10f}" for value in prior_closes)
    return MaEvaluation(current, previous, sha256(serialized.encode("utf-8")).hexdigest())


def evaluate_strategy(*, strategy: DailyMaStrategy, ma: MaEvaluation) -> StrategyDecision:
    """Evaluate entry and normal exit independently so both can occur in a batch."""
    direction = strategy.direction.upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")

    entry_cross = ma.crossed_up(strategy.entry_fast_ma, strategy.entry_slow_ma) if direction == "LONG" else ma.crossed_down(strategy.entry_fast_ma, strategy.entry_slow_ma)
    exit_cross = ma.crossed_down(strategy.exit_fast_ma, strategy.exit_slow_ma) if direction == "LONG" else ma.crossed_up(strategy.exit_fast_ma, strategy.exit_slow_ma)
    trend_passed = True
    if strategy.trend_ma is not None:
        trend_now, trend_previous = ma.values_now[strategy.trend_ma], ma.values_previous[strategy.trend_ma]
        trend_passed = trend_now > trend_previous if direction == "LONG" else trend_now < trend_previous
    return StrategyDecision(entry=entry_cross and trend_passed, normal_exit=exit_cross, trend_passed=trend_passed)


def day20_triggered(*, direction: str, source_close: float, previous_official_close: float) -> bool:
    """Frozen V0.3 DAY20 trigger based on a completed source minute close."""
    if source_close <= 0 or previous_official_close <= 0:
        return False
    if direction.upper() == "LONG":
        # Multiply rather than compare a floating percentage at the exact 20%
        # boundary; 80 / 100 - 1 can be represented as -0.199999... .
        return source_close <= previous_official_close * 0.80
    if direction.upper() == "SHORT":
        return source_close >= previous_official_close * 1.20
    raise ValueError("direction must be LONG or SHORT")
