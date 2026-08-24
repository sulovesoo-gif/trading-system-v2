"""PostgreSQL persistence for Daily MA V0.3 LIVE NO_SEND preparation only."""

from __future__ import annotations

from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from .live_nosend import CapitalReservation, NoSendIntent, NoSendOrderRequest


class PostgresDailyMaLiveNoSendStore:
    def __init__(self, connection_factory, *, commit: bool = True) -> None:
        self._connection_factory = connection_factory
        self._commit = commit

    def prepare(self, *, intent: NoSendIntent, execution_stock_code: str,
                strategy_instance_id: str, execution_target_time, global_trade_yn: str) -> tuple[NoSendOrderRequest, bool]:
        if global_trade_yn != "N":
            raise ValueError("Daily MA LIVE NO_SEND requires GLOBAL_TRADE_YN=N")
        request = NoSendOrderRequest(intent.request_key, intent.intent_key, execution_stock_code,
                                     intent.side, intent.quantity, execution_target_time)
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO daily_strategy_live_order_intent
                   (intent_id,intent_key,live_trade_id,paper_trade_id,strategy_id,signal_event_key,intent_type,
                    exit_reason,source_event_time,requested_quantity,reference_price,requested_notional,lifecycle_status)
                   VALUES (%s,%s,NULL,%s,%s,%s,%s,NULLIF(%s,''),%s,%s,%s,%s,'NO_SEND_VALIDATED')
                   ON CONFLICT (intent_key) DO NOTHING RETURNING intent_id""",
                (str(uuid5(NAMESPACE_URL, "daily-ma-live-intent|" + intent.intent_key)), intent.intent_key,
                 intent.paper_trade_id, intent.strategy_id, intent.signal_event_key, intent.intent_type,
                 "" if intent.intent_type == "ENTRY" else str(intent.exit_reason or "NORMAL_EXIT"), intent.source_event_time,
                 intent.quantity, intent.reference_price, intent.reference_price * intent.quantity),
            )
            created = cursor.fetchone() is not None
            cursor.execute(
                """INSERT INTO daily_strategy_live_order_request
                   (order_request_id,request_key,intent_id,strategy_instance_id,execution_stock_code,side,quantity,
                    order_type,execution_target_time,request_status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'MARKET_REFERENCE_ONLY',%s,'NO_SEND_VALIDATED')
                   ON CONFLICT (request_key) DO NOTHING""",
                (str(uuid5(NAMESPACE_URL, "daily-ma-live-request|" + request.request_key)), request.request_key,
                 str(uuid5(NAMESPACE_URL, "daily-ma-live-intent|" + intent.intent_key)), strategy_instance_id,
                 execution_stock_code, intent.side, intent.quantity, execution_target_time),
            )
            if intent.intent_type == "ENTRY":
                cursor.execute(
                    """INSERT INTO daily_strategy_live_capital_reservation
                       (reservation_id,intent_id,reserved_amount,reservation_status)
                       VALUES (%s,%s,%s,'RESERVED') ON CONFLICT (intent_id) DO NOTHING""",
                    (str(uuid5(NAMESPACE_URL, "daily-ma-live-reservation|" + intent.intent_key)),
                     str(uuid5(NAMESPACE_URL, "daily-ma-live-intent|" + intent.intent_key)),
                     Decimal(intent.reference_price) * intent.quantity),
                )
            if self._commit:
                connection.commit()
        return request, created
