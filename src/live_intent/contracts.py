"""Intent-only LIVE runtime contracts.  These objects never represent orders or fills."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping
from uuid import uuid5, NAMESPACE_URL


class IntentType(str, Enum):
    ENTRY_INTENT = "ENTRY_INTENT"
    EXIT_INTENT = "EXIT_INTENT"


class IntentStatus(str, Enum):
    CREATED = "CREATED"
    BLOCKED = "BLOCKED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED_SIMULATION = "CANCELLED_SIMULATION"


class RuntimeStatus(str, Enum):
    FLAT = "FLAT"
    ENTRY_INTENT = "ENTRY_INTENT"
    OPEN_SIMULATED = "OPEN_SIMULATED"
    EXIT_INTENT = "EXIT_INTENT"
    CLOSED_SIMULATED = "CLOSED_SIMULATED"
    BLOCKED = "BLOCKED"


def idempotency_key(*, strategy_instance_id: str, intent_type: IntentType, source_decision_id: str,
                    signal_time: datetime, execution_target_time: datetime) -> str:
    material = "|".join((strategy_instance_id, intent_type.value, source_decision_id, signal_time.isoformat(), execution_target_time.isoformat()))
    return sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LiveIntent:
    intent_id: str
    idempotency_key: str
    strategy_instance_id: str
    strategy_code: str
    strategy_version: str
    code_commit: str | None
    source_decision_id: str
    intent_type: IntentType
    signal_stock_code: str
    signal_direction: str
    execution_stock_code: str
    execution_direction: str
    signal_time: datetime
    decision_time: datetime
    execution_target_time: datetime
    reason_code: str
    decision_evidence: Mapping[str, Any]
    data_quality_status: str
    runtime_state_before: RuntimeStatus
    runtime_state_after: RuntimeStatus
    status: IntentStatus = IntentStatus.CREATED
    created_at: datetime = field(default_factory=datetime.now)

    @staticmethod
    def build(**kwargs: Any) -> "LiveIntent":
        key = idempotency_key(
            strategy_instance_id=kwargs["strategy_instance_id"], intent_type=kwargs["intent_type"],
            source_decision_id=kwargs["source_decision_id"], signal_time=kwargs["signal_time"],
            execution_target_time=kwargs["execution_target_time"],
        )
        return LiveIntent(intent_id=str(uuid5(NAMESPACE_URL, f"live-intent|{key}")), idempotency_key=key, **kwargs)


@dataclass(frozen=True)
class RuntimeState:
    strategy_instance_id: str
    status: RuntimeStatus = RuntimeStatus.FLAT
    strategy_state: Mapping[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class LiveAudit:
    event_type: str
    strategy_instance_id: str
    at: datetime
    reason: str
    detail: Mapping[str, Any] = field(default_factory=dict)
    source_decision_id: str | None = None
