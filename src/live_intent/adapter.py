"""Intent-only forward adapter.  It delegates all strategy rules to StrategyCore."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from src.strategy_core import HistoricalDataProvider, StrategyCore
from src.strategy_core.contracts import DecisionType, SignalDecision
from src.strategy_core.registry import StrategyDefinition

from .contracts import IntentStatus, IntentType, LiveAudit, LiveIntent, RuntimeState, RuntimeStatus
from .persistence import LiveIntentStore
from .quality import DataQualityGate, MarketContext


@dataclass(frozen=True)
class LiveStrategyInstance:
    strategy_instance_id: str
    definition: StrategyDefinition
    entry_group: str | None = None
    enabled: bool = True


class LiveStrategyAdapter:
    """Completed RAW -> quality gate -> Core Decision -> persistent intent/audit.

    No strategy condition, order service, broker client, capital, or position is
    implemented here.  Grouping is registry metadata and lets S3 variants share
    one generic Core entry evaluation.
    """

    def __init__(self, *, provider: HistoricalDataProvider, instances: Iterable[LiveStrategyInstance],
                 store: LiveIntentStore, quality_gate: DataQualityGate | None = None) -> None:
        self.provider, self.instances, self.store = provider, tuple(instance for instance in instances if instance.enabled), store
        self.quality_gate = quality_gate or DataQualityGate()
        self.entry_core_calls = 0
        self.entry_core_calls_by_group: dict[str, int] = {}

    def start(self, at: datetime) -> None:
        self.store.recover()
        self.store.audit(LiveAudit("PROCESS_STARTED", "GLOBAL", at, "intent-only runtime started"))

    def stop(self, at: datetime) -> None:
        self.store.audit(LiveAudit("PROCESS_STOPPED", "GLOBAL", at, "intent-only runtime stopped"))

    def process_completed_day(self, trading_date: str, *, context: MarketContext = MarketContext()) -> None:
        groups: dict[str, list[LiveStrategyInstance]] = {}
        for instance in self.instances:
            key = instance.entry_group or instance.strategy_instance_id
            groups.setdefault(key, []).append(instance)
        for group_key, members in groups.items():
            representative = members[0]
            source = self.provider.bars(representative.definition.signal_stock_code, trading_date)
            if not source:
                continue
            core = StrategyCore(representative.definition)
            required = self._required_lookback(representative.definition)
            quality = self.quality_gate.evaluate_entry(required_lookback=required, evaluated_at=source[-1].time, completed_bars=source, context=context)
            if not quality.entry_allowed:
                for member in members:
                    self._block_entry(member, source[-1].time, quality.status, quality.reason, quality.detail)
                continue
            self.entry_core_calls += 1
            self.entry_core_calls_by_group[group_key] = self.entry_core_calls_by_group.get(group_key, 0) + 1
            decisions = core.entry_decisions(source)
            for decision in decisions:
                for member in members:
                    self._entry(member, decision, quality.status)
                    self._exit_if_open(member, core=StrategyCore(member.definition), entry=decision, source=source, context=context)

    @staticmethod
    def _required_lookback(definition: StrategyDefinition) -> int:
        # Metadata only: this selects a generic history contract, not strategy logic.
        return int(definition.entry_params.get("required_lookback", 20 if definition.entry_params.get("rvol_threshold") else 30))

    def _entry(self, instance: LiveStrategyInstance, decision: SignalDecision, quality_status: str) -> None:
        state = self.store.runtime_state(instance.strategy_instance_id)
        if state.status not in {RuntimeStatus.FLAT, RuntimeStatus.CLOSED_SIMULATED, RuntimeStatus.BLOCKED}:
            return
        if decision.target_time is None:
            return
        # Target time remains the source-bar contract.  The adapter only
        # verifies that the execution product has that exact completed bar; it
        # never searches for or substitutes a different timestamp.
        if self.provider.bar_at(instance.definition.execution_stock_code, decision.target_time) is None:
            self.store.audit(LiveAudit("DATA_ERROR", instance.strategy_instance_id, decision.signal_time,
                                       "execution target bar unavailable", {"execution_target_time": decision.target_time.isoformat()}, decision.shared_entry_decision_id or decision.decision_id))
            return
        intent = LiveIntent.build(
            strategy_instance_id=instance.strategy_instance_id, strategy_code=instance.definition.strategy_code,
            strategy_version=instance.definition.strategy_version, code_commit=instance.definition.code_commit,
            source_decision_id=decision.shared_entry_decision_id or decision.decision_id, intent_type=IntentType.ENTRY_INTENT,
            signal_stock_code=decision.signal_stock_code, signal_direction=decision.signal_direction,
            execution_stock_code=decision.execution_stock_code, execution_direction=decision.execution_direction,
            signal_time=decision.signal_time, decision_time=decision.signal_time, execution_target_time=decision.target_time,
            reason_code=decision.entry_reason or "ENTRY", decision_evidence=decision.evidence,
            data_quality_status=quality_status, runtime_state_before=state.status, runtime_state_after=RuntimeStatus.OPEN_SIMULATED,
            status=IntentStatus.CREATED,
        )
        _, created = self.store.create_intent_and_transition(intent, RuntimeState(instance.strategy_instance_id, RuntimeStatus.OPEN_SIMULATED))
        self.store.audit(LiveAudit("ENTRY_INTENT_CREATED" if created else "DUPLICATE_INTENT_SUPPRESSED", instance.strategy_instance_id, decision.signal_time, intent.reason_code, {"idempotency_key": intent.idempotency_key}, intent.source_decision_id))

    def _exit_if_open(self, instance: LiveStrategyInstance, *, core: StrategyCore, entry: SignalDecision, source: tuple, context: MarketContext) -> None:
        state = self.store.runtime_state(instance.strategy_instance_id)
        if state.status != RuntimeStatus.OPEN_SIMULATED:
            return
        persisted_entries = [intent for intent in self.store.intents_for(instance.strategy_instance_id) if intent.intent_type == IntentType.ENTRY_INTENT]
        if not persisted_entries:
            return
        persisted_entry = max(persisted_entries, key=lambda intent: intent.created_at)
        if persisted_entry.signal_time != entry.signal_time:
            return
        execution = self.provider.bars(instance.definition.execution_stock_code, entry.signal_time.date().isoformat())
        quality = self.quality_gate.evaluate_exit(required_lookback=max(1, entry.required_lookback), evaluated_at=source[-1].time, completed_bars=source, context=context)
        if quality.status == "EXIT_DATA_UNCERTAIN":
            self.store.audit(LiveAudit("EXIT_DATA_UNCERTAIN", instance.strategy_instance_id, source[-1].time, quality.reason, quality.detail, persisted_entry.source_decision_id))
            return
        decision = core.exit_decision(entry, source, execution)
        if decision.decision_type != DecisionType.EXIT or decision.target_time is None:
            return
        intent = LiveIntent.build(
            strategy_instance_id=instance.strategy_instance_id, strategy_code=instance.definition.strategy_code,
            strategy_version=instance.definition.strategy_version, code_commit=instance.definition.code_commit,
            source_decision_id=decision.decision_id, intent_type=IntentType.EXIT_INTENT,
            signal_stock_code=decision.signal_stock_code, signal_direction=decision.signal_direction,
            execution_stock_code=decision.execution_stock_code, execution_direction=decision.execution_direction,
            signal_time=entry.signal_time, decision_time=decision.signal_time, execution_target_time=decision.target_time,
            reason_code=decision.exit_reason or "EXIT", decision_evidence=decision.evidence,
            data_quality_status=quality.status, runtime_state_before=state.status, runtime_state_after=RuntimeStatus.CLOSED_SIMULATED,
            status=IntentStatus.CREATED,
        )
        _, created = self.store.create_intent_and_transition(intent, RuntimeState(instance.strategy_instance_id, RuntimeStatus.CLOSED_SIMULATED))
        self.store.audit(LiveAudit("EXIT_INTENT_CREATED" if created else "DUPLICATE_INTENT_SUPPRESSED", instance.strategy_instance_id, decision.signal_time, intent.reason_code, {"idempotency_key": intent.idempotency_key}, intent.source_decision_id))

    def _block_entry(self, instance: LiveStrategyInstance, at: datetime, status: str, reason: str, detail: Mapping[str, object]) -> None:
        before = self.store.runtime_state(instance.strategy_instance_id)
        self.store.save_state(RuntimeState(instance.strategy_instance_id, RuntimeStatus.BLOCKED))
        self.store.audit(LiveAudit("ENTRY_BLOCKED_DATA_QUALITY", instance.strategy_instance_id, at, status, {"reason": reason, **detail}))
