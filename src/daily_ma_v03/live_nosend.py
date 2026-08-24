"""Daily MA V0.3 durable-no-send planning contracts.

This boundary deliberately has no broker adapter or transport dependency.
It models only intent, order-request preparation, and entry reservation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5


def _hash(material: str) -> str:
    return sha256(material.encode("utf-8")).hexdigest()


def entry_intent_key(*, strategy_id: str, signal_event_key: str) -> str:
    return _hash(f"DAILY_MA_V03|ENTRY|{strategy_id}|{signal_event_key}")


def exit_intent_key(*, paper_trade_id: int, exit_reason: str, source_event_time: datetime) -> str:
    return _hash(f"DAILY_MA_V03|EXIT|{paper_trade_id}|{exit_reason}|{source_event_time.isoformat()}")


@dataclass(frozen=True)
class NoSendIntent:
    intent_key: str
    paper_trade_id: int
    strategy_id: str
    signal_event_key: str
    intent_type: str
    side: str
    quantity: int
    reference_price: Decimal
    source_event_time: datetime
    exit_reason: str | None = None
    status: str = "NO_SEND_VALIDATED"
    live_trade_id: int | None = None

    @property
    def request_key(self) -> str:
        return _hash(f"DAILY_MA_V03|REQUEST|{self.intent_key}|{self.side}")

    @property
    def reservation_amount(self) -> Decimal:
        return self.reference_price * self.quantity if self.intent_type == "ENTRY" else Decimal("0")


@dataclass(frozen=True)
class NoSendOrderRequest:
    request_key: str
    intent_key: str
    execution_stock_code: str
    side: str
    quantity: int
    execution_target_time: datetime
    status: str = "NO_SEND_VALIDATED"
    broker_order_id: None = None
    broker_order_number: None = None


@dataclass(frozen=True)
class CapitalReservation:
    intent_key: str
    reserved_amount: Decimal
    consumed_amount: Decimal = Decimal("0")
    released_amount: Decimal = Decimal("0")
    status: str = "RESERVED"

    @property
    def remaining_reserved_amount(self) -> Decimal:
        return self.reserved_amount - self.consumed_amount - self.released_amount


class InMemoryDailyMaLiveNoSendStore:
    """Deterministic fixture store; duplicate planning returns the same rows."""

    def __init__(self) -> None:
        self.intents: dict[str, NoSendIntent] = {}
        self.requests: dict[str, NoSendOrderRequest] = {}
        self.reservations: dict[str, CapitalReservation] = {}

    def prepare(self, *, intent: NoSendIntent, execution_stock_code: str,
                strategy_instance_id: str, execution_target_time: datetime,
                global_trade_yn: str) -> tuple[NoSendOrderRequest, bool]:
        if global_trade_yn != "N":
            raise ValueError("Daily MA LIVE NO_SEND requires GLOBAL_TRADE_YN=N")
        if intent.quantity <= 0 or intent.reference_price <= 0:
            raise ValueError("quantity and reference price are required")
        existing = self.requests.get(intent.request_key)
        if existing is not None:
            return existing, False
        self.intents.setdefault(intent.intent_key, intent)
        request = NoSendOrderRequest(intent.request_key, intent.intent_key, execution_stock_code,
                                     intent.side, intent.quantity, execution_target_time)
        self.requests[request.request_key] = request
        if intent.intent_type == "ENTRY":
            self.reservations.setdefault(intent.intent_key, CapitalReservation(intent.intent_key, intent.reservation_amount))
        return request, True


class DailyMaLiveNoSendRuntime:
    """Bridges a durable PAPER entry event to a LIVE no-send plan only."""

    def __init__(self, *, store, global_trade_yn: str = "N") -> None:
        self.store = store
        self.global_trade_yn = global_trade_yn

    def plan_entry(self, *, paper_trade_id: int, strategy_id: str, signal_event_key: str,
                   execution_stock_code: str, strategy_instance_id: str,
                   quantity: int, reference_price: Decimal, signal_time: datetime,
                   execution_target_time: datetime, operation_status: str,
                   reconciliation_healthy: bool) -> tuple[NoSendOrderRequest | None, str]:
        if operation_status != "LIVE":
            return None, "OPERATION_NOT_LIVE"
        if not reconciliation_healthy:
            return None, "RECONCILIATION_REQUIRED"
        key = entry_intent_key(strategy_id=strategy_id, signal_event_key=signal_event_key)
        intent = NoSendIntent(key, paper_trade_id, strategy_id, signal_event_key, "ENTRY", "BUY",
                              quantity, reference_price, signal_time)
        request, _ = self.store.prepare(intent=intent, execution_stock_code=execution_stock_code,
                                        strategy_instance_id=strategy_instance_id,
                                        execution_target_time=execution_target_time,
                                        global_trade_yn=self.global_trade_yn)
        return request, "NO_SEND_VALIDATED"

    def plan_exit(self, *, paper_trade_id: int, strategy_id: str, signal_event_key: str,
                  execution_stock_code: str, strategy_instance_id: str, quantity: int,
                  reference_price: Decimal, source_event_time: datetime, exit_reason: str,
                  ownership_remaining: int, live_actual_closed: bool = False) -> tuple[NoSendOrderRequest | None, str]:
        if live_actual_closed and exit_reason == "NORMAL_EXIT":
            return None, "LIVE_ALREADY_CLOSED"
        if ownership_remaining < quantity or quantity <= 0:
            return None, "OWNERSHIP_REQUIRED"
        key = exit_intent_key(paper_trade_id=paper_trade_id, exit_reason=exit_reason, source_event_time=source_event_time)
        intent = NoSendIntent(key, paper_trade_id, strategy_id, signal_event_key, "EXIT", "SELL", quantity,
                              reference_price, source_event_time, exit_reason)
        request, _ = self.store.prepare(intent=intent, execution_stock_code=execution_stock_code,
                                        strategy_instance_id=strategy_instance_id,
                                        execution_target_time=source_event_time,
                                        global_trade_yn=self.global_trade_yn)
        return request, "NO_SEND_VALIDATED"
