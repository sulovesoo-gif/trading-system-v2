"""Exactly-once actual-fill capital settlement for a future approved SEND lane."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL,uuid5


@dataclass(frozen=True)
class SettlementAmounts:
    entry_filled_amount:Decimal
    exit_filled_amount:Decimal
    buy_fee:Decimal=Decimal('0')
    sell_fee:Decimal=Decimal('0')
    sell_tax:Decimal=Decimal('0')
    other_cost:Decimal=Decimal('0')
    @property
    def gross_realized_pnl(self):return self.exit_filled_amount-self.entry_filled_amount
    @property
    def net_realized_pnl(self):
        return self.gross_realized_pnl-self.buy_fee-self.sell_fee-self.sell_tax-self.other_cost


class PostgresMinuteMaCapitalStore:
    def __init__(self,connection_factory):self.connection_factory=connection_factory
    def apply_settlement(self,*,minute_live_trade_id:int,amounts:SettlementAmounts,
                         settled_at:datetime)->bool:
        settlement_id=str(uuid5(NAMESPACE_URL,f"minute-ma-settlement|{minute_live_trade_id}"))
        with self.connection_factory() as connection,connection.cursor() as cursor:
            cursor.execute("""SELECT minute_path_id,capital_epoch_no,trade_status,capital_applied_yn
                               FROM minute_ma_live_trade WHERE minute_live_trade_id=%s FOR UPDATE""",
                           (minute_live_trade_id,));trade=cursor.fetchone()
            if trade is None or trade[2]!='CLOSED':raise ValueError('CLOSED_LIVE_TRADE_REQUIRED')
            path_id,epoch_no=trade[0],trade[1]
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                           (f"minute-ma-capital|{path_id}|{epoch_no}",))
            cursor.execute("""INSERT INTO minute_ma_live_capital_settlement(
              settlement_id,minute_live_trade_id,minute_path_id,capital_epoch_no,entry_filled_amount,
              exit_filled_amount,gross_realized_pnl,buy_fee,sell_fee,sell_tax,other_cost,net_realized_pnl,settled_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(minute_live_trade_id) DO NOTHING RETURNING settlement_id""",
              (settlement_id,minute_live_trade_id,path_id,epoch_no,amounts.entry_filled_amount,
               amounts.exit_filled_amount,amounts.gross_realized_pnl,amounts.buy_fee,amounts.sell_fee,
               amounts.sell_tax,amounts.other_cost,amounts.net_realized_pnl,settled_at))
            created=cursor.fetchone() is not None
            if created:
                cursor.execute("""UPDATE minute_ma_compound_capital
                  SET cumulative_net_realized_pnl=cumulative_net_realized_pnl+%s,
                      strategy_compound_capital=epoch_initial_capital+cumulative_net_realized_pnl+%s,
                      version=version+1,updated_at=%s
                  WHERE minute_path_id=%s AND capital_epoch_no=%s""",
                  (amounts.net_realized_pnl,amounts.net_realized_pnl,settled_at,path_id,epoch_no))
                if cursor.rowcount!=1:raise ValueError('CAPITAL_EPOCH_REQUIRED')
                cursor.execute("""UPDATE minute_ma_live_trade SET entry_filled_amount=%s,
                  exit_filled_amount=%s,gross_realized_pnl=%s,net_realized_pnl=%s,
                  capital_applied_yn='Y',updated_at=%s WHERE minute_live_trade_id=%s""",
                  (amounts.entry_filled_amount,amounts.exit_filled_amount,amounts.gross_realized_pnl,
                   amounts.net_realized_pnl,settled_at,minute_live_trade_id))
            connection.commit();return created
