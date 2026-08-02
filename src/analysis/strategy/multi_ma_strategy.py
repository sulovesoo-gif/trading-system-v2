"""단독·누적 분할 다중 MA 가상 전략 상태 전이."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from src.analysis.event.multi_ma_event import MultiMaSignal


ONE_THIRD = Decimal("0.333333333333")


@dataclass(frozen=True)
class TradeAction:
    action: str
    direction: str
    weight: Decimal
    signal_type: str | None


@dataclass
class StrategyState:
    direction: str = "FLAT"
    weight: Decimal = Decimal("0")
    applied_signals: set[str] = field(default_factory=set)


def apply_single_signal(state: StrategyState, signal: MultiMaSignal, *, accepted_type: str) -> list[TradeAction]:
    if signal.signal_type != accepted_type:
        return []
    if state.direction == signal.direction:
        return []
    actions: list[TradeAction] = []
    if state.direction != "FLAT":
        actions.append(TradeAction("CLOSE", state.direction, state.weight, signal.signal_type))
    state.direction, state.weight, state.applied_signals = signal.direction, Decimal("1"), {signal.signal_type}
    actions.append(TradeAction("OPEN", signal.direction, Decimal("1"), signal.signal_type))
    return actions


def apply_accumulated(state: StrategyState, signals: Iterable[MultiMaSignal]) -> list[TradeAction]:
    events = list(signals)
    if not events:
        return []
    directions = {event.direction for event in events}
    if len(directions) != 1:
        raise ValueError("동일 스냅샷에서 반대 방향 타점이 동시에 발생하면 데이터 오류로 처리합니다.")
    direction = events[0].direction
    signal_types = {event.signal_type for event in events}
    actions: list[TradeAction] = []
    if state.direction not in ("FLAT", direction):
        actions.append(TradeAction("CLOSE", state.direction, state.weight, None))
        state.direction, state.weight, state.applied_signals = "FLAT", Decimal("0"), set()
    if state.direction == "FLAT":
        state.direction = direction
    new_types = signal_types - state.applied_signals
    if not new_types:
        return actions
    before = state.weight
    state.applied_signals.update(new_types)
    state.weight = min(Decimal("1"), Decimal(len(state.applied_signals)) * ONE_THIRD)
    delta = state.weight - before
    if delta > 0:
        actions.append(TradeAction("OPEN", direction, delta, ",".join(sorted(new_types))))
    return actions
