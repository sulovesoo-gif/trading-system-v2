"""Durable one-share Forward planning.  No broker import or send path exists."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5


class PostgresForwardNoSendPlanner:
    """Idempotently persists one-share Forward order-ready plans as PLANNED."""

    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    def plan(self, *, candidate_id: str, strategy_instance_id: str, execution_stock_code: str,
             source_decision_id: str, target_time: datetime, reference_price: float) -> bool:
        if reference_price <= 0:
            raise ValueError("reference price must be positive")
        key = sha256(f"FORWARD|{candidate_id}|{strategy_instance_id}|{source_decision_id}|BUY|{execution_stock_code}".encode()).hexdigest()
        intent_id = str(uuid5(NAMESPACE_URL, "forward-intent|" + key))
        request_id = str(uuid5(NAMESPACE_URL, "forward-order|" + key))
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO live_strategy_intent
                (intent_id,idempotency_key,strategy_instance_id,strategy_code,strategy_version,source_decision_id,intent_type,
                 signal_stock_code,signal_direction,execution_stock_code,execution_direction,signal_time,decision_time,execution_target_time,
                 reason_code,decision_evidence,data_quality_status,runtime_state_before,runtime_state_after,status)
                VALUES(%s,%s,%s,'FORWARD_OBSERVATION','FROZEN_20260818',%s,'ENTRY_INTENT',%s,'LONG',%s,'LONG',%s,%s,%s,
                       'FORWARD_ENTRY',%s::jsonb,'PASS','FLAT','OPEN_SIMULATED','CREATED')
                ON CONFLICT(idempotency_key) DO NOTHING RETURNING intent_id""",
                (intent_id, key, strategy_instance_id, source_decision_id, execution_stock_code, execution_stock_code,
                 target_time, target_time, target_time, json.dumps({"forward_candidate_id": candidate_id, "broker_send_eligible": False})))
            inserted = cursor.fetchone() is not None
            if inserted:
                cursor.execute("""INSERT INTO live_order_request
                    (order_request_id,idempotency_key,strategy_instance_id,source_intent_id,source_decision_id,execution_stock_code,side,
                     requested_notional,requested_quantity,reference_price,order_type,execution_target_time,strategy_capital_before,
                     reserved_capital,safety_status,status,reason,detail)
                    VALUES(%s,%s,%s,%s,%s,%s,'BUY',%s,1,%s,'FORWARD_NO_SEND',%s,0,0,'GLOBAL_TRADE_DISABLED','PLANNED',
                            'FORWARD_ONE_SHARE_NO_SEND',%s::jsonb)
                    ON CONFLICT(idempotency_key) DO NOTHING""",
                    (request_id, key, strategy_instance_id, intent_id, source_decision_id, execution_stock_code, reference_price,
                     reference_price, target_time, json.dumps({"forward_candidate_id": candidate_id, "broker_send_eligible": False})))
            connection.commit()
        return inserted
