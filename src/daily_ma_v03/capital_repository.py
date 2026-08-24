"""Postgres V0.4 capital persistence; contains no broker send capability."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from .capital import AvailableCash, CapitalEpoch, SettlementAmounts, decide_entry
from .live_nosend import NoSendIntent, NoSendOrderRequest


class PostgresDailyMaCapitalStore:
    def __init__(self, connection_factory, *, commit: bool = True) -> None:
        self._connection_factory, self._commit = connection_factory, commit

    def current_capital(self, *, strategy_id: str, capital_epoch_no: int) -> CapitalEpoch:
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT epoch_initial_capital,cumulative_net_realized_pnl
                                FROM daily_strategy_compound_capital
                               WHERE strategy_id=%s AND capital_epoch_no=%s""", (strategy_id, capital_epoch_no))
            row = cursor.fetchone()
        if row is None:
            raise ValueError("STRATEGY_CAPITAL_EPOCH_REQUIRED")
        return CapitalEpoch(strategy_id, capital_epoch_no, Decimal(row[0]), Decimal(row[1]))

    def apply_settlement(self, *, live_trade_id: int, strategy_id: str, capital_epoch_no: int,
                         amounts: SettlementAmounts, settled_at: datetime) -> bool:
        """Apply a CLOSED trade's net P&L exactly once, keyed by live_trade_id."""
        settlement_id = str(uuid5(NAMESPACE_URL, f"daily-ma-v04-settlement|{live_trade_id}"))
        with self._connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"daily-ma-v04-capital|{strategy_id}|{capital_epoch_no}",))
            cursor.execute("""SELECT trade_status,strategy_id,capital_epoch_no,capital_settled_at
                                FROM daily_strategy_live_trade WHERE live_trade_id=%s FOR UPDATE""", (live_trade_id,))
            trade = cursor.fetchone()
            if trade is None or trade[0] != "CLOSED" or str(trade[1]) != strategy_id or int(trade[2]) != capital_epoch_no:
                raise ValueError("CLOSED_LIVE_TRADE_REQUIRED")
            cursor.execute("""INSERT INTO daily_strategy_live_capital_settlement
                              (settlement_id,live_trade_id,strategy_id,capital_epoch_no,entry_filled_amount,exit_filled_amount,
                               gross_realized_pnl,buy_fee,sell_fee,sell_tax,other_cost_amount,net_realized_pnl,settled_at)
                              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                              ON CONFLICT (live_trade_id) DO NOTHING RETURNING settlement_id""",
                           (settlement_id, live_trade_id, strategy_id, capital_epoch_no, amounts.entry_filled_amount,
                            amounts.exit_filled_amount, amounts.gross_realized_pnl, amounts.buy_fee, amounts.sell_fee,
                            amounts.sell_tax, amounts.other_cost_amount, amounts.net_realized_pnl, settled_at))
            created = cursor.fetchone() is not None
            if created:
                cursor.execute("""UPDATE daily_strategy_compound_capital
                                   SET cumulative_net_realized_pnl=cumulative_net_realized_pnl+%s,
                                       strategy_compound_capital=epoch_initial_capital+cumulative_net_realized_pnl+%s,
                                       version=version+1,updated_at=%s
                                 WHERE strategy_id=%s AND capital_epoch_no=%s""",
                               (amounts.net_realized_pnl, amounts.net_realized_pnl, settled_at, strategy_id, capital_epoch_no))
                cursor.execute("""UPDATE daily_strategy_live_trade
                                   SET realized_pnl=%s,capital_settled_at=%s WHERE live_trade_id=%s""",
                               (amounts.net_realized_pnl, settled_at, live_trade_id))
            if self._commit:
                connection.commit()
        return created

    def plan_entry_no_send(self, *, intent: NoSendIntent, execution_stock_code: str,
                           strategy_instance_id: str, execution_target_time: datetime,
                           capital_epoch_no: int, available_cash: AvailableCash,
                           locally_reserved_amount: Decimal = Decimal("0")) -> tuple[NoSendOrderRequest | None, str]:
        """Persist either one no-send plan or a durable, never-retried cash skip."""
        if intent.intent_type != "ENTRY":
            raise ValueError("ENTRY intent required")
        with self._connection_factory() as connection, connection.cursor() as cursor:
            # Serialize only if the broker response does not itself account for
            # pending orders.  This also makes the decision restart-safe.
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("daily-ma-v04-shared-cash",))
            cursor.execute("""SELECT epoch_initial_capital,cumulative_net_realized_pnl
                                FROM daily_strategy_compound_capital
                               WHERE strategy_id=%s AND capital_epoch_no=%s FOR UPDATE""",
                           (intent.strategy_id, capital_epoch_no))
            row = cursor.fetchone()
            if row is None:
                return None, "STRATEGY_CAPITAL_EPOCH_REQUIRED"
            capital = CapitalEpoch(intent.strategy_id, capital_epoch_no, Decimal(row[0]), Decimal(row[1]))
            cursor.execute("""SELECT skip_reason FROM daily_strategy_live_entry_skip
                               WHERE strategy_id=%s AND signal_event_key=%s FOR UPDATE""",
                           (intent.strategy_id, intent.signal_event_key))
            previous_skip = cursor.fetchone()
            if previous_skip is not None:
                return None, str(previous_skip[0])
            cursor.execute("SELECT intent_id FROM daily_strategy_live_order_intent WHERE intent_key=%s FOR UPDATE", (intent.intent_key,))
            if cursor.fetchone() is not None:
                return NoSendOrderRequest(intent.request_key, intent.intent_key, execution_stock_code, "BUY", intent.quantity, execution_target_time), "NO_SEND_VALIDATED"
            decision = decide_entry(capital=capital, available_cash=available_cash,
                                    reference_price=intent.reference_price, locally_reserved_amount=locally_reserved_amount)
            if decision.status != "NO_SEND_VALIDATED":
                cursor.execute("""INSERT INTO daily_strategy_live_entry_skip
                                  (skip_id,strategy_id,paper_trade_id,signal_event_key,intent_key,capital_epoch_no,
                                   strategy_compound_capital_at_signal,planned_quantity,planned_notional,
                                   available_cash_snapshot,skip_reason,detail)
                                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,jsonb_build_object('cash_includes_pending_reservations',%s))
                                  ON CONFLICT (strategy_id,signal_event_key) DO NOTHING""",
                               (str(uuid5(NAMESPACE_URL, "daily-ma-v04-skip|" + intent.intent_key)), intent.strategy_id,
                                intent.paper_trade_id, intent.signal_event_key, intent.intent_key, capital_epoch_no,
                                decision.capital_at_signal, decision.quantity, decision.planned_notional,
                                available_cash.amount, decision.status, available_cash.includes_pending_order_reservations))
                if self._commit:
                    connection.commit()
                return None, decision.status
            request = NoSendOrderRequest(intent.request_key, intent.intent_key, execution_stock_code, "BUY", decision.quantity, execution_target_time)
            intent_id = str(uuid5(NAMESPACE_URL, "daily-ma-live-intent|" + intent.intent_key))
            cursor.execute("""INSERT INTO daily_strategy_live_order_intent
                              (intent_id,intent_key,paper_trade_id,strategy_id,signal_event_key,intent_type,source_event_time,
                               requested_quantity,reference_price,requested_notional,lifecycle_status,capital_epoch_no,
                               strategy_compound_capital_at_signal,available_cash_snapshot,cash_gate_checked_at)
                              VALUES (%s,%s,%s,%s,%s,'ENTRY',%s,%s,%s,%s,'NO_SEND_VALIDATED',%s,%s,%s,CURRENT_TIMESTAMP)""",
                           (intent_id, intent.intent_key, intent.paper_trade_id, intent.strategy_id, intent.signal_event_key,
                            intent.source_event_time, decision.quantity, intent.reference_price, decision.planned_notional,
                            capital_epoch_no, decision.capital_at_signal, available_cash.amount))
            cursor.execute("""INSERT INTO daily_strategy_live_order_request
                              (order_request_id,request_key,intent_id,strategy_instance_id,execution_stock_code,side,quantity,
                               order_type,execution_target_time,request_status)
                              VALUES (%s,%s,%s,%s,%s,'BUY',%s,'MARKET_REFERENCE_ONLY',%s,'NO_SEND_VALIDATED')""",
                           (str(uuid5(NAMESPACE_URL, "daily-ma-live-request|" + request.request_key)), request.request_key,
                            intent_id, strategy_instance_id, execution_stock_code, decision.quantity, execution_target_time))
            cursor.execute("""INSERT INTO daily_strategy_live_capital_reservation
                              (reservation_id,intent_id,reserved_amount,reservation_status)
                              VALUES (%s,%s,%s,'RESERVED')""",
                           (str(uuid5(NAMESPACE_URL, "daily-ma-live-reservation|" + intent.intent_key)), intent_id,
                            decision.planned_notional))
            if self._commit:
                connection.commit()
        return request, "NO_SEND_VALIDATED"
