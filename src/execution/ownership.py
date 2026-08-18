"""Logical ownership and broker-net reconciliation.

This module is deliberately free of strategy and transport imports.  A broker
account holds a net quantity by product; V2 ownership remains separate by
lane/id and only a fill allocation may change a logical position.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from hashlib import sha256
from typing import Mapping


class OwnershipError(ValueError):
    pass


class ExecutionLane(str, Enum):
    LIVE = "LIVE"
    FORWARD = "FORWARD"
    SMOKE = "ORDER_SMOKE_TEST"
    OTHER = "OTHER"


@dataclass(frozen=True)
class OwnershipKey:
    lane: ExecutionLane
    ownership_id: str
    stock_code: str

    def __post_init__(self) -> None:
        if not self.ownership_id or not self.stock_code:
            raise OwnershipError("ownership id and stock code are required")


@dataclass(frozen=True)
class LogicalPosition:
    owner: OwnershipKey
    quantity: int = 0
    average_cost: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    last_fill_at: object | None = None
    version: int = 0


@dataclass(frozen=True)
class FillAllocation:
    broker_order_id: str
    broker_trade_id: str
    owner: OwnershipKey
    side: str
    quantity: int
    price: Decimal
    fee: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    other_cost: Decimal = Decimal("0")
    filled_at: object | None = None

    @property
    def idempotency_key(self) -> str:
        return sha256(f"{self.broker_order_id}|{self.broker_trade_id}".encode()).hexdigest()


@dataclass(frozen=True)
class ReconciliationResult:
    stock_code: str
    broker_net_quantity: int
    attributed_quantity: int
    unattributed_quantity: int
    status: str


class InMemoryOwnershipLedger:
    """Deterministic append-only model for the durable ownership repository."""

    def __init__(self) -> None:
        self._positions: dict[OwnershipKey, LogicalPosition] = {}
        self._fills: dict[str, FillAllocation] = {}
        self.audit: list[tuple[str, str]] = []

    def position(self, owner: OwnershipKey) -> LogicalPosition:
        return self._positions.get(owner, LogicalPosition(owner))

    def positions(self) -> tuple[LogicalPosition, ...]:
        return tuple(self._positions.values())

    def apply_fill(self, fill: FillAllocation) -> tuple[LogicalPosition, bool]:
        if fill.side not in {"BUY", "SELL"} or fill.quantity <= 0 or fill.price < 0:
            raise OwnershipError("invalid fill allocation")
        existing = self._fills.get(fill.idempotency_key)
        if existing is not None:
            self.audit.append(("FILL_DUPLICATE_SUPPRESSED", fill.idempotency_key))
            return self.position(fill.owner), False
        position = self.position(fill.owner)
        if fill.side == "SELL" and fill.quantity > position.quantity:
            raise OwnershipError("SELL exceeds this logical ownership")
        if fill.side == "BUY":
            gross_old = position.average_cost * position.quantity
            gross_new = fill.price * fill.quantity + fill.fee + fill.other_cost
            quantity = position.quantity + fill.quantity
            updated = replace(
                position, quantity=quantity, average_cost=(gross_old + gross_new) / quantity,
                last_fill_at=fill.filled_at, version=position.version + 1,
            )
        else:
            proceeds = fill.price * fill.quantity - fill.fee - fill.tax - fill.other_cost
            realized = position.realized_pnl + proceeds - position.average_cost * fill.quantity
            quantity = position.quantity - fill.quantity
            updated = replace(
                position, quantity=quantity,
                average_cost=position.average_cost if quantity else Decimal("0"),
                realized_pnl=realized, last_fill_at=fill.filled_at, version=position.version + 1,
            )
        self._fills[fill.idempotency_key] = fill
        self._positions[fill.owner] = updated
        self.audit.append(("LOGICAL_POSITION_UPDATED", fill.idempotency_key))
        return updated, True

    def reconcile(self, broker_net_by_stock: Mapping[str, int]) -> tuple[ReconciliationResult, ...]:
        stocks = set(broker_net_by_stock) | {position.owner.stock_code for position in self._positions.values()}
        results = []
        for stock in sorted(stocks):
            attributed = sum(position.quantity for position in self._positions.values() if position.owner.stock_code == stock)
            broker = int(broker_net_by_stock.get(stock, 0))
            unattributed = broker - attributed
            results.append(ReconciliationResult(stock, broker, attributed, unattributed, "PASS" if not unattributed else "UNATTRIBUTED"))
        return tuple(results)
