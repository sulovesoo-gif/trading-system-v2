"""Pure PAPER trade lifecycle calculations for normal and DAY20 exits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import ActualExitKind, choose_actual_exit


@dataclass(frozen=True)
class ExitPlan:
    kind: ActualExitKind
    trigger_time: datetime | None
    execution_time: datetime
    execution_price: float


@dataclass(frozen=True)
class PaperReturn:
    return_pct: float | None
    fixed_basis_pnl: float | None


def select_actual_exit(*, day20_trigger_time: datetime | None,
                       day20_execution_time: datetime | None, day20_execution_price: float | None,
                       normal_execution_time: datetime, normal_execution_price: float) -> ExitPlan:
    """Enforce the approved strict trigger-vs-normal-execution precedence."""
    kind = choose_actual_exit(day20_trigger_time=day20_trigger_time,
                              normal_exit_execution_time=normal_execution_time)
    if kind is ActualExitKind.DAY20:
        if day20_execution_time is None or day20_execution_price is None:
            # No actual execution bar means DAY20 remains a pending durable
            # transition; it must never invent a price.
            raise ValueError("DAY20 actual exit requires an actual execution bar")
        return ExitPlan(kind, day20_trigger_time, day20_execution_time, day20_execution_price)
    return ExitPlan(ActualExitKind.NORMAL, None, normal_execution_time, normal_execution_price)


def paper_return(*, entry_price: float, exit_price: float, basis_amount: float = 1_000_000.0) -> PaperReturn:
    if entry_price <= 0 or exit_price <= 0 or basis_amount <= 0:
        return PaperReturn(None, None)
    ratio = exit_price / entry_price - 1.0
    return PaperReturn(ratio * 100.0, basis_amount * ratio)


def normal_delta(*, actual: PaperReturn, normal: PaperReturn) -> PaperReturn:
    """V0.3 comparison metrics, defined only when both exact prices exist."""
    if actual.return_pct is None or normal.return_pct is None:
        return PaperReturn(None, None)
    return PaperReturn(normal.return_pct - actual.return_pct,
                       (normal.fixed_basis_pnl or 0.0) - (actual.fixed_basis_pnl or 0.0))
