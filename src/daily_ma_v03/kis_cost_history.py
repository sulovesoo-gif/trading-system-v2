"""Read-only KIS product-day realized-profit cost source for Daily MA.

The supplied KIS account workbook identifies ``TTTC8715R`` as
``inquire-period-trade-profit``.  With one date and one ``PDNO`` it returns
the product-day totals used by V0.4.2.  The API provides no finalization flag;
callers must persist it as PENDING_BROKER_COST until a separately approved
finalization schedule is available.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from .broker_cost_allocation import BrokerCostTotals


@dataclass(frozen=True)
class DailyMaProductDayBrokerCosts:
    trade_date: date
    execution_stock_code: str
    totals: BrokerCostTotals
    broker_snapshot_at: datetime
    final: bool = False


class DailyMaKISProductDayCostLookup:
    path = "/uapi/domestic-stock/v1/trading/inquire-period-trade-profit"
    tr_id = "TTTC8715R"

    def __init__(self, *, client, account, clock=datetime.now) -> None:
        self.client, self.account, self.clock = client, account, clock

    def lookup(self, *, trade_date: date, execution_stock_code: str) -> DailyMaProductDayBrokerCosts:
        day = trade_date.strftime("%Y%m%d")
        response = self.client.get(path=self.path, tr_id=self.tr_id, custtype="P", params={
            "CANO": self.account.cano, "ACNT_PRDT_CD": self.account.account_product_code,
            "PDNO": execution_stock_code, "INQR_STRT_DT": day, "INQR_END_DT": day,
            "SORT_DVSN": "00", "INQR_DVSN": "00", "CBLC_DVSN": "00",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        })
        # The product/day restriction means output2's buy/sell totals are for
        # this one authoritative product-day, not an account-wide aggregate.
        totals = response.get("output2") or {}
        value = lambda key: Decimal(str(totals.get(key, "0") or "0"))
        return DailyMaProductDayBrokerCosts(
            trade_date, execution_stock_code,
            BrokerCostTotals(
                buy_fee=value("buy_fee_smtl"), sell_fee=value("sll_fee_smtl"),
                sell_tax=value("sll_tltx_smtl"),
            ),
            self.clock(), False,
        )
