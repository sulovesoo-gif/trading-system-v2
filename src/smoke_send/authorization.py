"""Non-forgeable-in-normal-use capabilities for the phase-7C send path.

The module intentionally exports no capability factory.  Only
``Phase7CSmokeRuntime`` imports the private factory after its durable approval
compare-and-swap succeeds.  Adapter and transport receive a one-shot permit,
not an approval id or a mode flag supplied by a caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from threading import RLock
from typing import TYPE_CHECKING

from src.broker.contracts import BrokerOrder

if TYPE_CHECKING:
    from .runtime import ActualApproval


class SendAuthorizationError(RuntimeError):
    """Raised before entering a phase-7C transport without its capability."""


_CONTEXT_ISSUER = object()
_PERMIT_ISSUER = object()


@dataclass
class _AuthorizedSendContext:
    """Runtime-only capability created from the CAS-returned CONSUMED row."""

    approval_id: str
    idempotency_key: str
    strategy_instance_id: str
    active_stock_code: str
    side: str
    quantity: int
    allowed_date: date
    allowed_time_from: time
    allowed_time_to: time
    _issuer: object = field(repr=False)
    _lock: RLock = field(default_factory=RLock, repr=False)
    _issued: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self._issuer is not _CONTEXT_ISSUER:
            raise SendAuthorizationError("phase-7C context must be runtime-issued")

    def _issue_transport_permit(self, order: BrokerOrder, issuer: object):
        if issuer is not _PERMIT_ISSUER:
            raise SendAuthorizationError("transport permit issuer rejected")
        _assert_order_matches_context(order, self)
        with self._lock:
            if self._issued:
                raise SendAuthorizationError("phase-7C context already used")
            self._issued = True
        return _TransportPermit(self, _PERMIT_ISSUER)


@dataclass(frozen=True)
class _TransportPermit:
    """Adapter-issued, one-use permit accepted by the KIS POST transport."""

    context: _AuthorizedSendContext
    _issuer: object = field(repr=False)

    def __post_init__(self) -> None:
        if self._issuer is not _PERMIT_ISSUER:
            raise SendAuthorizationError("phase-7C transport permit rejected")


def _context_from_consumed_approval(approval: "ActualApproval") -> _AuthorizedSendContext:
    """Private factory: only a successful CAS result may obtain a context."""
    if getattr(approval, "status", None) != "CONSUMED":
        raise SendAuthorizationError("approval must be durably CONSUMED")
    return _AuthorizedSendContext(
        approval_id=approval.approval_id,
        idempotency_key=approval.broker_idempotency_key,
        strategy_instance_id=approval.strategy_instance_id,
        active_stock_code=approval.active_stock_code,
        side=approval.side,
        quantity=approval.quantity,
        allowed_date=approval.allowed_date,
        allowed_time_from=approval.allowed_time_from,
        allowed_time_to=approval.allowed_time_to,
        _issuer=_CONTEXT_ISSUER,
    )


def issue_transport_permit(context: object, order: BrokerOrder) -> _TransportPermit:
    """Adapter boundary: reject caller values unless they carry the capability."""
    if not isinstance(context, _AuthorizedSendContext):
        raise SendAuthorizationError("phase-7C authorized context required")
    return context._issue_transport_permit(order, _PERMIT_ISSUER)


def validate_transport_permit(permit: object, order: BrokerOrder) -> None:
    """Transport boundary: refuse direct POST calls and mismatched requests."""
    if not isinstance(permit, _TransportPermit) or permit._issuer is not _PERMIT_ISSUER:
        raise SendAuthorizationError("phase-7C adapter transport permit required")
    _assert_order_matches_context(order, permit.context)


def _assert_order_matches_context(order: BrokerOrder, context: _AuthorizedSendContext) -> None:
    if (
        order.order_request_id != context.approval_id
        or order.client_order_key != context.idempotency_key
        or order.strategy_instance_id != context.strategy_instance_id
        or order.execution_stock_code != context.active_stock_code
        or order.side != context.side
        or order.quantity != context.quantity
    ):
        raise SendAuthorizationError("phase-7C context and broker order mismatch")
