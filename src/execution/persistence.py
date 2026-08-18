"""PostgreSQL persistence for append-only fills and per-owner positions.

This is intentionally an execution-accounting boundary: it neither invokes a
broker nor decides whether an order may be sent.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from .ownership import FillAllocation, LogicalPosition, OwnershipError, OwnershipKey, ReconciliationResult


class PostgresOwnershipStore:
    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    def apply_fill(self, fill: FillAllocation) -> tuple[LogicalPosition, bool]:
        if fill.side not in {"BUY", "SELL"} or fill.quantity <= 0 or fill.price < 0:
            raise OwnershipError("invalid fill allocation")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO execution_fill_allocation
                   (allocation_id, broker_order_id, broker_trade_id, ownership_type, ownership_id,
                    stock_code, side, quantity, fill_price, fee, tax, other_cost, filled_at, idempotency_key)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP), %s)
                   ON CONFLICT (idempotency_key) DO NOTHING
                   RETURNING allocation_id""",
                (str(uuid4()), fill.broker_order_id, fill.broker_trade_id, fill.owner.lane.value,
                 fill.owner.ownership_id, fill.owner.stock_code, fill.side, fill.quantity, fill.price,
                 fill.fee, fill.tax, fill.other_cost, fill.filled_at, fill.idempotency_key),
            )
            inserted = cursor.fetchone() is not None
            position = self._locked_position(cursor, fill.owner)
            if not inserted:
                connection.commit()
                return position, False
            if fill.side == "SELL" and fill.quantity > position.quantity:
                connection.rollback()
                raise OwnershipError("SELL exceeds this logical ownership")
            if fill.side == "BUY":
                quantity = position.quantity + fill.quantity
                average_cost = ((position.average_cost * position.quantity) + fill.price * fill.quantity + fill.fee + fill.other_cost) / quantity
                realized_pnl = position.realized_pnl
            else:
                quantity = position.quantity - fill.quantity
                average_cost = position.average_cost if quantity else Decimal("0")
                realized_pnl = position.realized_pnl + (fill.price * fill.quantity - fill.fee - fill.tax - fill.other_cost) - position.average_cost * fill.quantity
            cursor.execute(
                """INSERT INTO execution_logical_position
                   (ownership_type, ownership_id, stock_code, quantity, average_cost, realized_pnl, last_fill_at, version)
                   VALUES (%s,%s,%s,%s,%s,%s,COALESCE(%s,CURRENT_TIMESTAMP),1)
                   ON CONFLICT (ownership_type, ownership_id, stock_code) DO UPDATE SET
                    quantity=EXCLUDED.quantity, average_cost=EXCLUDED.average_cost,
                    realized_pnl=EXCLUDED.realized_pnl, last_fill_at=EXCLUDED.last_fill_at,
                    version=execution_logical_position.version+1, updated_at=CURRENT_TIMESTAMP""",
                (fill.owner.lane.value, fill.owner.ownership_id, fill.owner.stock_code, quantity,
                 average_cost, realized_pnl, fill.filled_at),
            )
            connection.commit()
            return replace(position, quantity=quantity, average_cost=average_cost, realized_pnl=realized_pnl,
                           last_fill_at=fill.filled_at, version=position.version + 1), True

    def position(self, owner: OwnershipKey) -> LogicalPosition:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            return self._locked_position(cursor, owner, lock=False)

    def reconcile(self, broker_net_by_stock: dict[str, int]) -> tuple[ReconciliationResult, ...]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT stock_code, COALESCE(SUM(quantity),0) FROM execution_logical_position GROUP BY stock_code")
            attributed = {str(stock): int(quantity) for stock, quantity in cursor.fetchall()}
            results = []
            for stock in sorted(set(attributed) | set(broker_net_by_stock)):
                broker = int(broker_net_by_stock.get(stock, 0)); owned = attributed.get(stock, 0); delta = broker - owned
                result = ReconciliationResult(stock, broker, owned, delta, "PASS" if delta == 0 else "UNATTRIBUTED")
                cursor.execute(
                    """INSERT INTO execution_reconciliation_audit
                       (stock_code, broker_net_quantity, attributed_quantity, unattributed_quantity, status, detail)
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
                    (stock, broker, owned, delta, result.status, "{}"),
                )
                results.append(result)
            connection.commit()
        return tuple(results)

    @staticmethod
    def _locked_position(cursor, owner: OwnershipKey, *, lock: bool = True) -> LogicalPosition:
        suffix = " FOR UPDATE" if lock else ""
        cursor.execute(
            "SELECT quantity, average_cost, realized_pnl, last_fill_at, version FROM execution_logical_position "
            "WHERE ownership_type=%s AND ownership_id=%s AND stock_code=%s" + suffix,
            (owner.lane.value, owner.ownership_id, owner.stock_code),
        )
        row = cursor.fetchone()
        if row is None:
            return LogicalPosition(owner)
        return LogicalPosition(owner, int(row[0]), Decimal(row[1]), Decimal(row[2]), row[3], int(row[4]))
