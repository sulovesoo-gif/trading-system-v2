"""Durable Daily MA submit claim and acknowledgement repository.

The database claim commits *before* the one allowed POST attempt.  A crash or
timeout therefore leaves an observable ``UNKNOWN_BROKER_STATE`` record rather
than a request that could be accidentally submitted again on restart.
"""

from __future__ import annotations

import json
from uuid import NAMESPACE_URL, uuid5

from src.broker.contracts import BrokerOrder, BrokerOrderStatus


class PostgresDailyMaActualSubmitStore:
    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    def discover_ready_request_keys(self) -> tuple[str, ...]:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT r.request_key FROM daily_strategy_live_order_request r
                              JOIN daily_strategy_live_order_intent i USING(intent_id)
                             WHERE r.request_status='NO_SEND_VALIDATED'
                               AND i.lifecycle_status='NO_SEND_VALIDATED'
                             ORDER BY r.created_at,r.request_key""")
            return tuple(str(row[0]) for row in cursor.fetchall())

    def pending_broker_orders(self):
        """Read-only polling inputs; only mapped Daily MA orders are eligible."""
        from datetime import date
        from types import SimpleNamespace
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT b.broker_order_id,b.broker_order_number,r.execution_stock_code,r.side,
                                     COALESCE(t.ownership_id,'')
                              FROM live_broker_order b
                              JOIN daily_strategy_live_broker_order_mapping m ON m.broker_order_id=b.broker_order_id
                              JOIN daily_strategy_live_order_request r ON r.order_request_id=b.order_request_id
                              LEFT JOIN daily_strategy_live_order_intent i USING(intent_id)
                              LEFT JOIN daily_strategy_live_trade t ON t.live_trade_id=i.live_trade_id
                             WHERE b.status IN ('ACCEPTED','PARTIALLY_FILLED','UNKNOWN_BROKER_STATE')
                             ORDER BY b.created_at""")
            return tuple(SimpleNamespace(broker_order_id=str(x[0]),broker_order_number=str(x[1]),stock_code=str(x[2]),side=str(x[3]),ownership_id=str(x[4]),order_date=date.today()) for x in cursor.fetchall())

    def claim(self, *, request_key: str) -> BrokerOrder | None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT r.order_request_id,r.strategy_instance_id,r.execution_stock_code,r.side,r.quantity,
                                     r.execution_target_time,r.request_status,i.intent_key
                              FROM daily_strategy_live_order_request r
                              JOIN daily_strategy_live_order_intent i USING(intent_id)
                             WHERE r.request_key=%s FOR UPDATE""", (request_key,))
            row = cursor.fetchone()
            if row is None or row[6] != "NO_SEND_VALIDATED":
                connection.commit()
                return None
            broker_id = str(uuid5(NAMESPACE_URL, "daily-ma-broker-order|" + request_key))
            payload = {"order_policy": "DAILY_MA_KRX_MARKET", "intent_key": str(row[7])}
            cursor.execute("""INSERT INTO live_broker_order
                              (broker_order_id,order_request_id,strategy_instance_id,execution_stock_code,side,quantity,
                               client_order_key,status,payload)
                              VALUES (%s,%s,%s,%s,%s,%s,%s,'SUBMITTING',%s::jsonb)
                              ON CONFLICT (order_request_id) DO NOTHING""",
                           (broker_id, row[0], row[1], row[2], row[3], row[4], request_key, json.dumps(payload)))
            cursor.execute("""UPDATE daily_strategy_live_order_request SET request_status='READY_FOR_BROKER',updated_at=CURRENT_TIMESTAMP
                              WHERE order_request_id=%s""", (row[0],))
            cursor.execute("""UPDATE daily_strategy_live_order_intent SET lifecycle_status='SUBMITTING',updated_at=CURRENT_TIMESTAMP
                              WHERE intent_key=%s""", (row[7],))
            connection.commit()
        return BrokerOrder(broker_id, str(row[0]), str(row[1]), str(row[2]), str(row[3]), int(row[4]), request_key,
                           BrokerOrderStatus.SUBMITTING, payload, created_at=row[5])

    def acknowledge(self, *, order: BrokerOrder, raw: dict) -> None:
        output = raw.get("output", {}) if isinstance(raw, dict) else {}
        number = str(output.get("ODNO", "")).strip()
        if not number:
            raise ValueError("DAILY_MA_ACK_ORDER_NUMBER_REQUIRED")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""UPDATE live_broker_order SET status='ACCEPTED',broker_order_number=%s
                              WHERE broker_order_id=%s AND status='SUBMITTING'""", (number, order.broker_order_id))
            cursor.execute("""INSERT INTO daily_strategy_live_broker_order_mapping(order_request_id,broker_order_id,broker_order_number)
                              VALUES (%s,%s,%s) ON CONFLICT (order_request_id) DO NOTHING""",
                           (order.order_request_id, order.broker_order_id, number))
            cursor.execute("""UPDATE daily_strategy_live_order_intent i SET lifecycle_status='ACCEPTED',updated_at=CURRENT_TIMESTAMP
                              FROM daily_strategy_live_order_request r
                             WHERE r.order_request_id=%s AND i.intent_id=r.intent_id""", (order.order_request_id,))
            connection.commit()

    def mark_unknown(self, *, order: BrokerOrder) -> None:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE live_broker_order SET status='UNKNOWN_BROKER_STATE' WHERE broker_order_id=%s", (order.broker_order_id,))
            cursor.execute("""UPDATE daily_strategy_live_order_intent i SET lifecycle_status='UNKNOWN_BROKER_STATE',updated_at=CURRENT_TIMESTAMP
                              FROM daily_strategy_live_order_request r WHERE r.order_request_id=%s AND i.intent_id=r.intent_id""",
                           (order.order_request_id,))
            connection.commit()
