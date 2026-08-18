"""No-send FORWARD_OBSERVATION runtime orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .contracts import ForwardCandidate


@dataclass(frozen=True)
class ForwardPlan:
    candidate_id: str
    strategy_instance_id: str
    execution_stock_code: str
    quantity: int = 1
    broker_send_eligible: bool = False


class ForwardObservationRuntime:
    """Reloads approved candidates and creates only fixed-one-share no-send plans.

    The injected evaluator is the public Strategy Core path.  The runtime owns
    orchestration and candidate lifecycle only; it cannot call a broker.
    """

    def __init__(self, *, registry, evaluator: Callable[[ForwardCandidate, datetime], bool], planner: Callable[[ForwardPlan], None]) -> None:
        self.registry = registry
        self.evaluator = evaluator
        self.planner = planner
        self._active: dict[str, ForwardCandidate] = {}

    def reload(self) -> tuple[ForwardCandidate, ...]:
        candidates = tuple(self.registry.active_candidates())
        self._active = {candidate.candidate_id: candidate for candidate in candidates}
        return candidates

    def run_cycle(self, *, at: datetime) -> tuple[ForwardPlan, ...]:
        self.reload()
        plans: list[ForwardPlan] = []
        for candidate in self._active.values():
            if not self.evaluator(candidate, at):
                continue
            plan = ForwardPlan(
                candidate_id=candidate.candidate_id,
                strategy_instance_id=candidate.strategy_reference,
                execution_stock_code=candidate.path.execution_stock_code,
            )
            self.planner(plan)
            plans.append(plan)
        return tuple(plans)
