"""Shared deterministic MA gap-transition contract.

Only the crossover direction is shared.  Daily and Minute runtimes retain
their own definitions of the previous/current source state and execution time.
"""
from __future__ import annotations

from enum import Enum


class GapTransition(str, Enum):
    UP_CROSS = "UP_CROSS"
    DOWN_CROSS = "DOWN_CROSS"
    UP_NEAR = "UP_NEAR"
    DOWN_NEAR = "DOWN_NEAR"
    NO_CROSS = "NO_CROSS"


def classify_gap_transition(*, previous_gap: float, current_gap: float,
                            near_threshold: float | None = None) -> GapTransition:
    """Classify one fast-minus-slow transition from previous to current.

    ``near_threshold`` must use the same unit as both gap arguments.  Runtime
    crossover callers omit it; Dashboard callers pass percentage-point gaps.
    """
    if previous_gap <= 0 < current_gap:
        return GapTransition.UP_CROSS
    if previous_gap >= 0 > current_gap:
        return GapTransition.DOWN_CROSS
    if near_threshold is None or near_threshold < 0:
        return GapTransition.NO_CROSS
    if -near_threshold <= current_gap < 0 and previous_gap < current_gap:
        return GapTransition.UP_NEAR
    if 0 < current_gap <= near_threshold and previous_gap > current_gap:
        return GapTransition.DOWN_NEAR
    return GapTransition.NO_CROSS
