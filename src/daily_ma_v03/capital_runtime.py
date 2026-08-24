"""V0.4 capital-aware bridge for the existing V0.3 no-send lane."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .capital import AvailableCash
from .live_nosend import NoSendIntent, entry_intent_key


class DailyMaV04CapitalNoSendRuntime:
    """Plans one entry or records a durable no-retry cash skip; never submits."""

    def __init__(self, *, capital_store, global_trade_yn: str = "N") -> None:
        self.capital_store, self.global_trade_yn = capital_store, global_trade_yn

    def plan_entry(self, *, paper_trade_id: int, strategy_id: str, signal_event_key: str,
                   execution_stock_code: str, strategy_instance_id: str,
                   reference_price: Decimal, signal_time: datetime, execution_target_time: datetime,
                   capital_epoch_no: int, available_cash: AvailableCash,
                   operation_status: str, reconciliation_healthy: bool,
                   locally_reserved_amount: Decimal = Decimal("0")):
        if self.global_trade_yn != "N":
            raise ValueError("Daily MA V0.4 no-send requires GLOBAL_TRADE_YN=N")
        if operation_status != "LIVE":
            return None, "OPERATION_NOT_LIVE"
        if not reconciliation_healthy:
            return None, "RECONCILIATION_REQUIRED"
        intent = NoSendIntent(entry_intent_key(strategy_id=strategy_id, signal_event_key=signal_event_key),
                              paper_trade_id, strategy_id, signal_event_key, "ENTRY", "BUY", 1,
                              reference_price, signal_time)
        return self.capital_store.plan_entry_no_send(intent=intent, execution_stock_code=execution_stock_code,
                                                     strategy_instance_id=strategy_instance_id,
                                                     execution_target_time=execution_target_time,
                                                     capital_epoch_no=capital_epoch_no,
                                                     available_cash=available_cash,
                                                     locally_reserved_amount=locally_reserved_amount)
