"""Persistence boundary; in-memory implementation is used only by tests/replay."""

from __future__ import annotations

import json
from typing import Protocol

from .contracts import LiveAudit, LiveIntent, RuntimeState


class LiveIntentStore(Protocol):
    def runtime_state(self, strategy_instance_id: str) -> RuntimeState: ...
    def create_intent_and_transition(self, intent: LiveIntent, state: RuntimeState) -> tuple[LiveIntent, bool]: ...
    def save_state(self, state: RuntimeState) -> None: ...
    def audit(self, event: LiveAudit) -> None: ...
    def recover(self) -> dict[str, RuntimeState]: ...
    def intents_for(self, strategy_instance_id: str) -> tuple[LiveIntent, ...]: ...


class InMemoryLiveIntentStore:
    """Transaction-like idempotency model mirroring the proposed DB unique key."""

    def __init__(self) -> None:
        self.intents: dict[str, LiveIntent] = {}
        self.states: dict[str, RuntimeState] = {}
        self.audits: list[LiveAudit] = []

    def runtime_state(self, strategy_instance_id: str) -> RuntimeState:
        return self.states.get(strategy_instance_id, RuntimeState(strategy_instance_id))

    def create_intent_and_transition(self, intent: LiveIntent, state: RuntimeState) -> tuple[LiveIntent, bool]:
        existing = self.intents.get(intent.idempotency_key)
        if existing is not None:
            return existing, False
        self.intents[intent.idempotency_key] = intent
        self.states[state.strategy_instance_id] = state
        return intent, True

    def save_state(self, state: RuntimeState) -> None:
        self.states[state.strategy_instance_id] = state

    def audit(self, event: LiveAudit) -> None:
        self.audits.append(event)

    def intents_for(self, strategy_instance_id: str) -> tuple[LiveIntent, ...]:
        return tuple(intent for intent in self.intents.values() if intent.strategy_instance_id == strategy_instance_id)

    def recover(self) -> dict[str, RuntimeState]:
        # A durable DB implementation derives the same state from the last
        # committed intent if a process crashes between write steps.
        for intent in self.intents.values():
            self.states[intent.strategy_instance_id] = RuntimeState(intent.strategy_instance_id, intent.runtime_state_after)
        return dict(self.states)


class PostgresLiveIntentStore:
    """DB-API persistence implementation for the additive phase-6 schema.

    The caller owns the connection factory and migration lifecycle.  The class
    intentionally has no database-settings, collector, order, or broker import.
    """

    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    def runtime_state(self, strategy_instance_id: str) -> RuntimeState:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT runtime_status, strategy_state, updated_at FROM live_strategy_runtime_state WHERE strategy_instance_id=%s", (strategy_instance_id,))
            row = cursor.fetchone()
        return RuntimeState(strategy_instance_id) if row is None else RuntimeState(strategy_instance_id, RuntimeStatus(row[0]), row[1], row[2])

    def create_intent_and_transition(self, intent: LiveIntent, state: RuntimeState) -> tuple[LiveIntent, bool]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO live_strategy_intent (
                intent_id,idempotency_key,strategy_instance_id,strategy_code,strategy_version,code_commit,source_decision_id,intent_type,
                signal_stock_code,signal_direction,execution_stock_code,execution_direction,signal_time,decision_time,execution_target_time,
                reason_code,decision_evidence,data_quality_status,runtime_state_before,runtime_state_after,status,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
            ON CONFLICT (idempotency_key) DO NOTHING RETURNING intent_id""", (
                intent.intent_id, intent.idempotency_key, intent.strategy_instance_id, intent.strategy_code, intent.strategy_version,
                intent.code_commit, intent.source_decision_id, intent.intent_type.value, intent.signal_stock_code, intent.signal_direction,
                intent.execution_stock_code, intent.execution_direction, intent.signal_time, intent.decision_time, intent.execution_target_time,
                intent.reason_code, json.dumps(intent.decision_evidence), intent.data_quality_status, intent.runtime_state_before.value,
                intent.runtime_state_after.value, intent.status.value, intent.created_at,
            ))
            inserted = cursor.fetchone() is not None
            if inserted:
                cursor.execute("""INSERT INTO live_strategy_runtime_state(strategy_instance_id,runtime_status,strategy_state,updated_at)
                    VALUES (%s,%s,%s::jsonb,%s)
                    ON CONFLICT(strategy_instance_id) DO UPDATE SET runtime_status=EXCLUDED.runtime_status,
                    strategy_state=EXCLUDED.strategy_state,updated_at=EXCLUDED.updated_at""",
                    (state.strategy_instance_id, state.status.value, json.dumps(state.strategy_state), state.updated_at))
            else:
                cursor.execute("SELECT intent_id,strategy_code,strategy_version,code_commit,source_decision_id,intent_type,signal_stock_code,signal_direction,execution_stock_code,execution_direction,signal_time,decision_time,execution_target_time,reason_code,decision_evidence,data_quality_status,runtime_state_before,runtime_state_after,status,created_at FROM live_strategy_intent WHERE idempotency_key=%s", (intent.idempotency_key,))
                row = cursor.fetchone()
                return self._row_to_intent(intent.strategy_instance_id, intent.idempotency_key, row), False
            connection.commit()
        return intent, True

    def save_state(self, state: RuntimeState) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO live_strategy_runtime_state(strategy_instance_id,runtime_status,strategy_state,updated_at)
                VALUES(%s,%s,%s::jsonb,%s) ON CONFLICT(strategy_instance_id) DO UPDATE SET runtime_status=EXCLUDED.runtime_status,
                strategy_state=EXCLUDED.strategy_state,updated_at=EXCLUDED.updated_at""", (state.strategy_instance_id, state.status.value, json.dumps(state.strategy_state), state.updated_at))
            connection.commit()

    def audit(self, event: LiveAudit) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO live_strategy_audit(event_type,strategy_instance_id,event_time,source_decision_id,reason,detail) VALUES(%s,%s,%s,%s,%s,%s::jsonb)", (event.event_type, event.strategy_instance_id, event.at, event.source_decision_id, event.reason, json.dumps(event.detail)))
            connection.commit()

    def recover(self) -> dict[str, RuntimeState]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT strategy_instance_id,runtime_status,strategy_state,updated_at FROM live_strategy_runtime_state")
            return {row[0]: RuntimeState(row[0], RuntimeStatus(row[1]), row[2], row[3]) for row in cursor.fetchall()}

    def intents_for(self, strategy_instance_id: str) -> tuple[LiveIntent, ...]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT intent_id,idempotency_key,strategy_code,strategy_version,code_commit,source_decision_id,intent_type,signal_stock_code,signal_direction,execution_stock_code,execution_direction,signal_time,decision_time,execution_target_time,reason_code,decision_evidence,data_quality_status,runtime_state_before,runtime_state_after,status,created_at FROM live_strategy_intent WHERE strategy_instance_id=%s", (strategy_instance_id,))
            return tuple(self._row_to_intent(strategy_instance_id, None, row) for row in cursor.fetchall())

    @staticmethod
    def _row_to_intent(instance: str, key: str | None, row) -> LiveIntent:
        return LiveIntent(row[0], key or row[1], instance, row[2], row[3], row[4], str(row[5]), IntentType(row[6]), row[7], row[8], row[9], row[10], row[11], row[12], row[13], row[14], row[15], row[16], RuntimeStatus(row[17]), RuntimeStatus(row[18]), IntentStatus(row[19]), row[20])
