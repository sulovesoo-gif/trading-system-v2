"""Path-scoped no-send Forward lifecycle; broker transport is deliberately absent."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from decimal import Decimal
from src.execution.ownership import ExecutionLane, FillAllocation, OwnershipKey

class ForwardState(str, Enum):
    FLAT = "FLAT"
    ENTRY_PLANNED = "ENTRY_PLANNED"
    BUY_PENDING = "BUY_PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    OPEN = "OPEN"
    EXIT_PLANNED = "EXIT_PLANNED"
    SELL_PENDING = "SELL_PENDING"
    CLOSED = "CLOSED"
    UNKNOWN_BROKER_STATE = "UNKNOWN_BROKER_STATE"
    BLOCKED = "BLOCKED"


@dataclass
class ForwardPathCycle:
    candidate_id: str
    stock_code: str
    state: ForwardState = ForwardState.FLAT
    active: bool = True

    def can_enter(self) -> bool:
        return self.active and self.state in {ForwardState.FLAT, ForwardState.CLOSED}

    def can_exit(self) -> bool:
        # Deactivation blocks new exposure only.  Existing ownership remains
        # managed through its own exit semantics until it is closed.
        return self.state in {
            ForwardState.OPEN,
            ForwardState.PARTIALLY_FILLED,
            ForwardState.EXIT_PLANNED,
            ForwardState.SELL_PENDING,
        }

    def plan_entry(self) -> None:
        if not self.can_enter():
            raise ValueError("new entry blocked for path state")
        self.state = ForwardState.ENTRY_PLANNED

    def plan_exit(self) -> None:
        if not self.can_exit():
            raise ValueError("exit unavailable for path state")
        self.state = ForwardState.EXIT_PLANNED

    def fill(self, store, *, broker_order_id: str, broker_trade_id: str, side: str, price) -> tuple[object, bool]:
        if side == "BUY" and self.state not in {ForwardState.ENTRY_PLANNED, ForwardState.BUY_PENDING}:
            raise ValueError("unexpected buy fill")
        if side == "SELL" and self.state not in {ForwardState.EXIT_PLANNED, ForwardState.SELL_PENDING}:
            raise ValueError("unexpected sell fill")
        owner = OwnershipKey(ExecutionLane.FORWARD, self.candidate_id, self.stock_code)
        position, inserted = store.apply_fill(
            FillAllocation(broker_order_id, broker_trade_id, owner, side, 1, Decimal(str(price)))
        )
        if inserted:
            self.state = ForwardState.OPEN if side == "BUY" else ForwardState.CLOSED
        return position, inserted
