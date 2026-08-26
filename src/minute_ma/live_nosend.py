"""Minute-MA adapter into the shared order-request/ownership layer.

This module is intentionally unable to submit a broker order.  It prepares a
durable shared request only when the minute path is LIVE and every existing
safety contract passes.  MINUTE_MA_LIVE_SEND remains disabled independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal,ROUND_FLOOR
from hashlib import sha256
from uuid import NAMESPACE_URL,uuid5


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MinuteMaNoSendResult:
    status: str
    intent_key: str
    request_id: str | None
    quantity: int


class PostgresMinuteMaNoSendAdapter:
    """Create one idempotent NO_SEND intent/request; broker POST is impossible."""

    def __init__(self,connection_factory) -> None:
        self.connection_factory=connection_factory

    def plan_entry(self,*,minute_path_id:int,minute_paper_trade_id:int,
                   signal_event_key:str,execution_stock_code:str,
                   reference_price:Decimal,available_cash:Decimal,
                   cash_includes_pending_reservations:bool,source_event_time:datetime) -> MinuteMaNoSendResult:
        intent_key=_digest(f"MINUTE_MA_V01|ENTRY|{minute_path_id}|{signal_event_key}")
        intent_id=str(uuid5(NAMESPACE_URL,"minute-ma-intent|"+intent_key))
        request_id=str(uuid5(NAMESPACE_URL,"minute-ma-request|"+intent_key))
        with self.connection_factory() as connection,connection.cursor() as cursor:
            cursor.execute("SELECT send_enabled FROM minute_ma_send_profile WHERE profile_code='MINUTE_MA_LIVE_SEND'")
            profile=cursor.fetchone()
            if profile is None or profile[0] != 'N':
                raise ValueError("MINUTE_MA_NO_SEND_PROFILE_REQUIRED")
            cursor.execute("""SELECT o.operation_id,o.operation_status,o.capital_epoch_no,
                                      c.strategy_compound_capital
                                 FROM minute_ma_operation o
                                 JOIN minute_ma_compound_capital c
                                   ON c.minute_path_id=o.minute_path_id AND c.capital_epoch_no=o.capital_epoch_no
                                WHERE o.minute_path_id=%s AND o.effective_to IS NULL FOR UPDATE""",(minute_path_id,))
            operation=cursor.fetchone()
            if operation is None or operation[1] != 'LIVE':
                return MinuteMaNoSendResult("OPERATION_NOT_LIVE",intent_key,None,0)
            operation_id,_,epoch_no,capital=operation
            cursor.execute("""SELECT status,unattributed_quantity
                                 FROM execution_reconciliation_audit
                                WHERE stock_code=%s ORDER BY checked_at DESC,reconciliation_id DESC LIMIT 1""",
                           (execution_stock_code,))
            reconciliation=cursor.fetchone()
            if reconciliation is None or reconciliation[0] != 'HEALTHY' or int(reconciliation[1]) != 0:
                return MinuteMaNoSendResult("RECONCILIATION_REQUIRED",intent_key,None,0)
            cursor.execute("""SELECT skip_reason FROM minute_ma_live_entry_skip
                                WHERE minute_path_id=%s AND signal_event_key=%s""",
                           (minute_path_id,signal_event_key))
            prior_skip=cursor.fetchone()
            if prior_skip:
                return MinuteMaNoSendResult(str(prior_skip[0]),intent_key,None,0)
            cursor.execute("SELECT requested_quantity FROM minute_ma_live_intent WHERE intent_key=%s",(intent_key,))
            existing=cursor.fetchone()
            if existing:
                return MinuteMaNoSendResult("NO_SEND_VALIDATED",intent_key,request_id,int(existing[0]))
            effective_cash=Decimal(available_cash)
            if not cash_includes_pending_reservations:
                cursor.execute("""SELECT COALESCE(sum(remaining_reserved_amount),0)
                                     FROM minute_ma_live_capital_reservation
                                    WHERE reservation_status IN ('RESERVED','PARTIALLY_CONSUMED')""")
                effective_cash=max(Decimal('0'),effective_cash-Decimal(cursor.fetchone()[0]))
            price=Decimal(reference_price)
            quantity=int((Decimal(capital)/price).to_integral_value(rounding=ROUND_FLOOR))
            notional=price*quantity
            if quantity<=0 or notional>effective_cash:
                reason='ZERO_QUANTITY' if quantity<=0 else 'INSUFFICIENT_AVAILABLE_CASH'
                cursor.execute("""INSERT INTO minute_ma_live_entry_skip(
                  skip_id,minute_path_id,minute_paper_trade_id,signal_event_key,capital_epoch_no,
                  capital_at_signal,planned_quantity,planned_notional,skip_reason)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                  (str(uuid5(NAMESPACE_URL,"minute-ma-skip|"+intent_key)),minute_path_id,
                   minute_paper_trade_id,signal_event_key,epoch_no,capital,quantity,notional,reason))
                connection.commit()
                return MinuteMaNoSendResult(reason,intent_key,None,quantity)
            strategy_instance=f"MINUTE_MA_PATH:{minute_path_id}:EPOCH:{epoch_no}"
            request_key=_digest(f"MINUTE_MA_V01|REQUEST|{intent_key}|BUY")
            cursor.execute("""INSERT INTO minute_ma_live_intent(
              intent_id,intent_key,minute_path_id,minute_paper_trade_id,intent_type,source_event_time,
              reference_price,requested_quantity,capital_at_signal,lifecycle_status)
              VALUES (%s,%s,%s,%s,'ENTRY',%s,%s,%s,%s,'NO_SEND_VALIDATED')
              ON CONFLICT(intent_key) DO NOTHING""",
              (intent_id,intent_key,minute_path_id,minute_paper_trade_id,source_event_time,
               price,quantity,capital))
            cursor.execute("""INSERT INTO live_order_request(
              order_request_id,idempotency_key,strategy_instance_id,source_intent_id,source_decision_id,
              execution_stock_code,side,requested_notional,requested_quantity,reference_price,order_type,
              execution_target_time,strategy_capital_before,reserved_capital,safety_status,status,reason,detail)
              VALUES (%s,%s,%s,%s,%s,%s,'BUY',%s,%s,%s,'MARKET_REFERENCE_ONLY',%s,%s,%s,
                      'NO_SEND_VALIDATED','NO_SEND_VALIDATED','MINUTE_MA_SEND_LOCKED',
                      jsonb_build_object('minute_path_id',%s,'operation_id',%s))
              ON CONFLICT(idempotency_key) DO NOTHING""",
              (request_id,request_key,strategy_instance,intent_id,
               str(uuid5(NAMESPACE_URL,"minute-ma-decision|"+intent_key)),execution_stock_code,
               notional,quantity,price,source_event_time,capital,notional,minute_path_id,operation_id))
            cursor.execute("""INSERT INTO minute_ma_live_order_link(intent_id,order_request_id)
                              VALUES (%s,%s) ON CONFLICT(intent_id) DO NOTHING""",(intent_id,request_id))
            cursor.execute("""INSERT INTO minute_ma_live_capital_reservation(
                              intent_id,reserved_amount,reservation_status)
                              VALUES (%s,%s,'RESERVED') ON CONFLICT(intent_id) DO NOTHING""",(intent_id,notional))
            connection.commit()
        return MinuteMaNoSendResult("NO_SEND_VALIDATED",intent_key,request_id,quantity)

    def plan_exit(self,*,minute_live_trade_id:int,execution_stock_code:str,
                  reference_price:Decimal,source_event_time:datetime,
                  exit_reason:str) -> MinuteMaNoSendResult:
        """Plan only the quantity owned by one minute-MA live trade."""
        material=f"MINUTE_MA_V01|EXIT|{minute_live_trade_id}|{exit_reason}|{source_event_time.isoformat()}"
        intent_key=_digest(material);intent_id=str(uuid5(NAMESPACE_URL,"minute-ma-intent|"+intent_key))
        request_id=str(uuid5(NAMESPACE_URL,"minute-ma-request|"+intent_key))
        with self.connection_factory() as connection,connection.cursor() as cursor:
            cursor.execute("SELECT send_enabled FROM minute_ma_send_profile WHERE profile_code='MINUTE_MA_LIVE_SEND'")
            profile=cursor.fetchone()
            if profile is None or profile[0]!='N':raise ValueError("MINUTE_MA_NO_SEND_PROFILE_REQUIRED")
            cursor.execute("""SELECT t.minute_path_id,t.minute_paper_trade_id,t.ownership_id,t.capital_at_signal
                                 FROM minute_ma_live_trade t
                                WHERE t.minute_live_trade_id=%s AND t.trade_status='OPEN' FOR UPDATE""",
                           (minute_live_trade_id,));trade=cursor.fetchone()
            if trade is None:return MinuteMaNoSendResult("OPEN_LIVE_TRADE_REQUIRED",intent_key,None,0)
            path_id,paper_trade_id,ownership_id,capital=trade
            cursor.execute("""SELECT quantity FROM execution_logical_position
                                WHERE ownership_type='MINUTE_MA' AND ownership_id=%s AND stock_code=%s""",
                           (ownership_id,execution_stock_code));position=cursor.fetchone()
            quantity=0 if position is None else int(position[0])
            if quantity<=0:return MinuteMaNoSendResult("OWNERSHIP_REQUIRED",intent_key,None,0)
            cursor.execute("""SELECT status,unattributed_quantity FROM execution_reconciliation_audit
                                WHERE stock_code=%s ORDER BY checked_at DESC,reconciliation_id DESC LIMIT 1""",
                           (execution_stock_code,));reconciliation=cursor.fetchone()
            if reconciliation is None or reconciliation[0]!='HEALTHY' or int(reconciliation[1])!=0:
                return MinuteMaNoSendResult("RECONCILIATION_REQUIRED",intent_key,None,0)
            cursor.execute("SELECT requested_quantity FROM minute_ma_live_intent WHERE intent_key=%s",(intent_key,))
            existing=cursor.fetchone()
            if existing:return MinuteMaNoSendResult("NO_SEND_VALIDATED",intent_key,request_id,int(existing[0]))
            price=Decimal(reference_price);request_key=_digest(f"MINUTE_MA_V01|REQUEST|{intent_key}|SELL")
            strategy_instance=f"MINUTE_MA_PATH:{path_id}:LIVE_TRADE:{minute_live_trade_id}"
            cursor.execute("""INSERT INTO minute_ma_live_intent(
              intent_id,intent_key,minute_path_id,minute_paper_trade_id,minute_live_trade_id,intent_type,
              source_event_time,reference_price,requested_quantity,capital_at_signal,lifecycle_status)
              VALUES (%s,%s,%s,%s,%s,'EXIT',%s,%s,%s,%s,'NO_SEND_VALIDATED')""",
              (intent_id,intent_key,path_id,paper_trade_id,minute_live_trade_id,source_event_time,price,quantity,capital))
            cursor.execute("""INSERT INTO live_order_request(
              order_request_id,idempotency_key,strategy_instance_id,source_intent_id,source_decision_id,
              execution_stock_code,side,requested_notional,requested_quantity,reference_price,order_type,
              execution_target_time,strategy_capital_before,reserved_capital,safety_status,status,reason,detail)
              VALUES (%s,%s,%s,%s,%s,%s,'SELL',%s,%s,%s,'MARKET_REFERENCE_ONLY',%s,%s,0,
                      'NO_SEND_VALIDATED','NO_SEND_VALIDATED',%s,
                      jsonb_build_object('minute_path_id',%s,'minute_live_trade_id',%s,'ownership_id',%s))
              ON CONFLICT(idempotency_key) DO NOTHING""",
              (request_id,request_key,strategy_instance,intent_id,
               str(uuid5(NAMESPACE_URL,"minute-ma-decision|"+intent_key)),execution_stock_code,
               price*quantity,quantity,price,source_event_time,capital,exit_reason,path_id,
               minute_live_trade_id,ownership_id))
            cursor.execute("""INSERT INTO minute_ma_live_order_link(intent_id,order_request_id)
                              VALUES (%s,%s) ON CONFLICT(intent_id) DO NOTHING""",(intent_id,request_id))
            connection.commit()
        return MinuteMaNoSendResult("NO_SEND_VALIDATED",intent_key,request_id,quantity)
