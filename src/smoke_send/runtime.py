"""7C-only single-submit runtime. No approval is created or enabled here."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time
from enum import Enum
from hashlib import sha256
from threading import RLock
from uuid import NAMESPACE_URL, uuid5

from src.broker import BrokerMode, BrokerOrder, BrokerOrderStatus, KisBrokerAdapter
from src.smoke_gate import ResolvedSmokeConfig
from .authorization import _context_from_consumed_approval, validate_transport_permit


class ApprovalStatus(str, Enum):
    NOT_APPROVED = "NOT_APPROVED"
    APPROVED_FOR_ONE_SUBMIT = "APPROVED_FOR_ONE_SUBMIT"
    CONSUMED = "CONSUMED"


def broker_idempotency_key(approval_id: str) -> str:
    return sha256(f"7C-1|{approval_id}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ActualApproval:
    approval_id: str
    strategy_instance_id: str
    active_stock_code: str
    allowed_date: date
    allowed_time_from: time
    allowed_time_to: time
    status: ApprovalStatus = ApprovalStatus.NOT_APPROVED
    broker_idempotency_key: str = ""
    broker_state: str = "NOT_SENT"
    side: str = "BUY"
    quantity: int = 1
    exchange: str = "KRX"
    order_division: str = "15"
    order_price: str = "0"

    def __post_init__(self):
        if not self.broker_idempotency_key:
            object.__setattr__(self, "broker_idempotency_key", broker_idempotency_key(self.approval_id))


@dataclass(frozen=True)
class SmokeGateState:
    global_trade_yn: str
    today_actual_submit_count: int
    open_order_count: int
    unknown_order_count: int


class InMemorySmokeApprovalStore:
    """Locking model for the durable SQL compare-and-swap operation."""

    def __init__(self):
        self._lock = RLock()
        self.approvals: dict[str, ActualApproval] = {}
        self.used_idempotency_keys: set[str] = set()
        self.audits: list[tuple[str, str]] = []

    def save(self, approval: ActualApproval) -> None:
        with self._lock:
            self.approvals[approval.approval_id] = approval

    def create_approved(self, *, approval: ActualApproval, config: ResolvedSmokeConfig) -> None:
        validate_approval_scope(approval=approval, config=config)
        with self._lock:
            if approval.approval_id in self.approvals or approval.broker_idempotency_key in self.used_idempotency_keys:
                raise ValueError("approval already exists")
            self.approvals[approval.approval_id] = approval

    def get(self, approval_id: str) -> ActualApproval | None:
        with self._lock:
            return self.approvals.get(approval_id)

    def consume_immediately_before_send(self, approval_id: str, key: str, *, stock_code: str, strategy_instance_id: str, side: str, quantity: int, exchange: str, order_division: str, order_price: str) -> ActualApproval | None:
        """Atomic compare-and-swap: validation is done before this call."""
        with self._lock:
            approval = self.approvals.get(approval_id)
            if (
                approval is None
                or approval.status is not ApprovalStatus.APPROVED_FOR_ONE_SUBMIT
                or approval.broker_idempotency_key != key
                or approval.active_stock_code != stock_code
                or approval.strategy_instance_id != strategy_instance_id
                or approval.side != side
                or approval.quantity != quantity
                or approval.exchange != exchange
                or approval.order_division != order_division
                or approval.order_price != order_price
                or key in self.used_idempotency_keys
            ):
                return None
            consumed = replace(approval, status=ApprovalStatus.CONSUMED)
            self.approvals[approval_id] = consumed
            self.used_idempotency_keys.add(key)
            self.audits.append(("APPROVAL_CONSUMED_BEFORE_SEND", approval_id))
            return consumed

    def mark_unknown(self, approval_id: str) -> None:
        self.mark_broker_state(approval_id, "UNKNOWN_BROKER_STATE")
        self.audits.append(("UNKNOWN_BROKER_STATE", approval_id))

    def mark_broker_state(self, approval_id: str, state: str) -> None:
        with self._lock:
            approval = self.approvals[approval_id]
            # Consumption is terminal irrespective of broker outcome.
            self.approvals[approval_id] = replace(approval, broker_state=state)


class PostgresSmokeApprovalStore:
    """Durable approval compare-and-swap store; no broker or KIS import."""

    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    def get(self, approval_id: str) -> ActualApproval | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """SELECT approval_id::text, strategy_instance_id, active_stock_code,
                          allowed_date, allowed_time_from, allowed_time_to, status,
                          broker_idempotency_key, broker_state, side, quantity,
                          exchange, order_division, order_price
                   FROM live_smoke_approval WHERE approval_id=%s""",
                (approval_id,),
            )
            row = cursor.fetchone()
        return None if row is None else self._row(row)

    def create_approved(self, *, approval: ActualApproval, config: ResolvedSmokeConfig) -> None:
        """Future explicit approval path; never called by a dry-run or runtime."""
        validate_approval_scope(approval=approval, config=config)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO live_smoke_approval
                   (approval_id, phase, strategy_instance_id, active_stock_code,
                    side, quantity, exchange, order_division, order_price,
                    allowed_date, allowed_time_from, allowed_time_to,
                    status, broker_idempotency_key)
                   VALUES (%s, '7C-1', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (approval.approval_id, approval.strategy_instance_id,
                 approval.active_stock_code, approval.side, approval.quantity,
                 approval.exchange, approval.order_division, approval.order_price,
                 approval.allowed_date, approval.allowed_time_from,
                 approval.allowed_time_to, approval.status.value,
                 approval.broker_idempotency_key),
            )
            connection.commit()

    def consume_immediately_before_send(self, approval_id: str, key: str, *, stock_code: str, strategy_instance_id: str, side: str, quantity: int, exchange: str, order_division: str, order_price: str) -> ActualApproval | None:
        # One SQL compare-and-swap is the lock/transaction boundary immediately
        # before the adapter enters transport.
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE live_smoke_approval
                   SET status='CONSUMED', consumed_at=CURRENT_TIMESTAMP
                   WHERE approval_id=%s
                     AND status='APPROVED_FOR_ONE_SUBMIT'
                     AND broker_idempotency_key=%s
                     AND active_stock_code=%s
                     AND strategy_instance_id=%s
                     AND side=%s
                     AND quantity=%s
                     AND exchange=%s
                     AND order_division=%s
                     AND order_price=%s
                   RETURNING approval_id::text, strategy_instance_id,
                             active_stock_code, allowed_date, allowed_time_from,
                             allowed_time_to, status, broker_idempotency_key,
                             broker_state, side, quantity, exchange, order_division, order_price""",
                (approval_id, key, stock_code, strategy_instance_id, side, quantity,
                 exchange, order_division, order_price),
            )
            row = cursor.fetchone()
            if row is not None:
                cursor.execute(
                    """INSERT INTO live_smoke_approval_audit
                        (approval_id,event_type,detail)
                        VALUES (%s,'APPROVAL_CONSUMED_BEFORE_SEND',%s::jsonb)""",
                    (approval_id, '{"transport_entered":false}'),
                )
            connection.commit()
        return None if row is None else self._row(row)

    def mark_unknown(self, approval_id: str) -> None:
        self.mark_broker_state(approval_id, "UNKNOWN_BROKER_STATE")

    def mark_broker_state(self, approval_id: str, state: str) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """UPDATE live_smoke_approval
                   SET broker_state=%s
                   WHERE approval_id=%s AND status='CONSUMED'""",
                (state, approval_id),
            )
            cursor.execute(
                """INSERT INTO live_smoke_approval_audit
                    (approval_id,event_type,detail)
                    VALUES (%s,%s,%s::jsonb)""",
                (approval_id, state, '{}'),
            )
            connection.commit()

    @staticmethod
    def _row(row) -> ActualApproval:
        return ActualApproval(
            approval_id=str(row[0]), strategy_instance_id=str(row[1]),
            active_stock_code=str(row[2]), allowed_date=row[3],
            allowed_time_from=row[4], allowed_time_to=row[5],
            status=ApprovalStatus(str(row[6])), broker_idempotency_key=str(row[7]),
            broker_state=str(row[8]), side=str(row[9]), quantity=int(row[10]),
            exchange=str(row[11]), order_division=str(row[12]), order_price=str(row[13]),
        )


class DeterministicTransport:
    """Test-only transport; it never imports KIS or opens a network connection."""

    def __init__(self, outcome: str = "ACK"):
        self.outcome = outcome
        self.send_calls = 0
        self.lookup_calls = 0
        self.seen_keys: set[str] = set()

    def submit_once(self, order: BrokerOrder, *, permit: object = None):
        validate_transport_permit(permit, order)
        if order.client_order_key in self.seen_keys:
            raise RuntimeError("DUPLICATE_TRANSPORT_KEY")
        self.seen_keys.add(order.client_order_key)
        self.send_calls += 1
        if self.outcome == "TIMEOUT":
            raise TimeoutError("deterministic timeout")
        if self.outcome == "REJECT":
            return {"rt_cd": "1", "msg_cd": "STUB_REJECT"}
        return {"rt_cd": "0", "odno": "STUB-ACK"}

    def lookup(self, key: str):
        self.lookup_calls += 1
        return {"idempotency_key": key, "status": "UNKNOWN"}


class Phase7CSmokeRuntime:
    """The only mode allowed to invoke a configured 7C transport callback."""

    def __init__(self, *, approvals: InMemorySmokeApprovalStore, adapter: KisBrokerAdapter):
        if adapter.mode is not BrokerMode.PHASE_7C_SMOKE_SEND:
            raise ValueError("7C runtime requires PHASE_7C_SMOKE_SEND adapter")
        self.approvals, self.adapter = approvals, adapter

    def submit_once(self, *, config: ResolvedSmokeConfig, approval_id: str, at: datetime, state: SmokeGateState):
        approval = self.approvals.get(approval_id)
        reason = self._validate(config=config, approval=approval, at=at, state=state)
        if reason is not None:
            return None, reason
        assert approval is not None
        # This is the last operation before entering the adapter/transport.
        consumed = self.approvals.consume_immediately_before_send(
            approval_id, approval.broker_idempotency_key,
            stock_code=config.active_stock_code,
            strategy_instance_id=config.strategy_instance_id,
            side=config.side,
            quantity=config.quantity,
            exchange=config.exchange,
            order_division=config.order_division,
            order_price=config.order_price,
        )
        if consumed is None:
            return None, "APPROVAL_ALREADY_CONSUMED"
        order = self._broker_order(consumed)
        authorized_context = _context_from_consumed_approval(consumed)
        try:
            response = self.adapter.submit(order, authorized_context=authorized_context)
        except TimeoutError:
            self.approvals.mark_unknown(approval_id)
            return None, "UNKNOWN_BROKER_STATE"
        if response.get("rt_cd") == "0":
            self.approvals.mark_broker_state(approval_id, "ACK_ACCEPTED")
            return response, "ACK"
        self.approvals.mark_broker_state(approval_id, "ACK_REJECTED")
        return response, "REJECTED"

    def recover(self, approval_id: str):
        approval = self.approvals.get(approval_id)
        if approval is None or approval.status is not ApprovalStatus.CONSUMED or approval.broker_state != "UNKNOWN_BROKER_STATE":
            return None
        return self.adapter.phase_7c_transport.lookup(approval.broker_idempotency_key)

    @staticmethod
    def _validate(*, config: ResolvedSmokeConfig, approval: ActualApproval | None, at: datetime, state: SmokeGateState) -> str | None:
        if not all(ok for ok, _ in config.validate()):
            return "RESOLVED_CONFIG_INVALID"
        if approval is None or approval.status is not ApprovalStatus.APPROVED_FOR_ONE_SUBMIT:
            return "ACTUAL_APPROVAL_REQUIRED"
        try:
            validate_approval_scope(approval=approval, config=config)
        except ValueError:
            return "APPROVAL_CONFIG_MISMATCH"
        if at.date() != config.allowed_date or not config.allowed_time_from <= at.time() <= config.allowed_time_to:
            return "TIME_WINDOW_BLOCKED"
        if state.today_actual_submit_count != 0 or state.open_order_count != 0 or state.unknown_order_count != 0:
            return "ORDER_STATE_BLOCKED"
        if state.global_trade_yn != "N":
            return "GLOBAL_TRADE_MUST_REMAIN_DISABLED"
        return None

    @staticmethod
    def _broker_order(approval: ActualApproval) -> BrokerOrder:
        broker_order_id = str(uuid5(NAMESPACE_URL, f"7c-broker-order|{approval.approval_id}"))
        payload = {
            "PDNO": approval.active_stock_code,
            "ORD_QTY": str(approval.quantity),
            "EXCG_ID_DVSN_CD": approval.exchange,
            "ORD_DVSN": approval.order_division,
            "ORD_UNPR": approval.order_price,
            "phase": "7C-1",
            "idempotency_key": approval.broker_idempotency_key,
        }
        return BrokerOrder(
            broker_order_id=broker_order_id,
            order_request_id=approval.approval_id,
            strategy_instance_id=approval.strategy_instance_id,
            execution_stock_code=approval.active_stock_code,
            side=approval.side,
            quantity=approval.quantity,
            client_order_key=approval.broker_idempotency_key,
            status=BrokerOrderStatus.SUBMITTING,
            payload=payload,
        )


def validate_approval_scope(*, approval: ActualApproval, config: ResolvedSmokeConfig) -> None:
    """Reject any approval/config mismatch before CAS can consume it."""
    if approval.status is not ApprovalStatus.APPROVED_FOR_ONE_SUBMIT:
        raise ValueError("approval must be approved for one submit")
    if approval.broker_idempotency_key != broker_idempotency_key(approval.approval_id):
        raise ValueError("approval idempotency key mismatch")
    if (
        approval.active_stock_code != config.active_stock_code
        or approval.strategy_instance_id != config.strategy_instance_id
        or approval.allowed_date != config.allowed_date
        or approval.allowed_time_from != config.allowed_time_from
        or approval.allowed_time_to != config.allowed_time_to
        or approval.side != config.side
        or approval.quantity != config.quantity
        or approval.exchange != config.exchange
        or approval.order_division != config.order_division
        or approval.order_price != config.order_price
    ):
        raise ValueError("approval scope does not match resolved config")
    if (approval.side, approval.quantity, approval.exchange,
        approval.order_division, approval.order_price) != ("BUY", 1, "KRX", "15", "0"):
        raise ValueError("7C-1 approval must be BUY one KRX IOC-best share at zero price")
