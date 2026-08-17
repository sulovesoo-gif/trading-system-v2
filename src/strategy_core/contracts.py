"""Serializable contracts shared by research, replay, and future LIVE adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5


class DecisionType(StrEnum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    HOLD = "HOLD"
    BLOCK = "BLOCK"
    NONE = "NONE"


def decision_id(*, strategy_code: str, signal_time: datetime, decision_type: DecisionType, scope: str = "") -> str:
    """A stable identifier: equal input/state produces the same decision identity."""
    material = f"{strategy_code}|{signal_time.isoformat()}|{decision_type}|{scope}"
    return str(uuid5(NAMESPACE_URL, material))


@dataclass(frozen=True)
class SignalDecision:
    decision_id: str
    strategy_id: int | None
    strategy_code: str
    strategy_version: str
    code_commit: str | None
    signal_stock_code: str
    signal_direction: str
    signal_time: datetime
    execution_stock_code: str
    execution_direction: str
    decision_type: DecisionType
    entry_reason: str | None = None
    exit_reason: str | None = None
    target_time: datetime | None = None
    reference_levels: Mapping[str, Any] = field(default_factory=dict)
    invalidation_levels: Mapping[str, Any] = field(default_factory=dict)
    required_lookback: int = 0
    parameter_snapshot: Mapping[str, Any] = field(default_factory=dict)
    data_quality_status: str = "UNVERIFIED"
    evidence: Mapping[str, Any] = field(default_factory=dict)
    shared_entry_decision_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, deterministic serialization without a persistence dependency."""
        value = asdict(self)
        for key in ("signal_time", "target_time"):
            if value[key] is not None:
                value[key] = value[key].isoformat()
        value["decision_type"] = self.decision_type.value
        return value
