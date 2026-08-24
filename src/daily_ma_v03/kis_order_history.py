"""Read-only Daily MA UNKNOWN recovery through KIS daily order/fill history.

The supplied official API workbook (v1_국내주식-005) identifies the current
live three-month TR as ``TTTC0081R``.  ``TTTC8001R`` is explicitly marked as
the old TR and is not used here.  A missing or ambiguous response never grants
permission to resend; it remains an unresolved broker state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


@dataclass(frozen=True)
class DailyMaBrokerHistoryOrder:
    order_date: str
    order_number: str
    order_branch: str
    stock_code: str
    side: str
    order_quantity: int
    average_fill_price: Decimal
    total_filled_quantity: int
    remaining_quantity: int
    rejected_quantity: int
    cancelled: bool
    order_time: str


class UnknownResolution(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNRESOLVED = "UNRESOLVED"


class DailyMaKISOrderHistoryLookup:
    path = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
    # v1_국내주식-005: current live <=3 month TR.  TTTC8001R is legacy.
    tr_id = "TTTC0081R"

    def __init__(self, *, client, account) -> None:
        self.client, self.account = client, account

    def orders_for_day(self, *, order_date: date, stock_code: str, side: str,
                       order_number: str = "") -> tuple[DailyMaBrokerHistoryOrder, ...]:
        side_code = {"BUY": "02", "SELL": "01"}.get(side)
        if side_code is None:
            raise ValueError("DAILY_MA_HISTORY_SIDE_REQUIRED")
        day = order_date.strftime("%Y%m%d")
        response = self.client.get(path=self.path, tr_id=self.tr_id, custtype="P", params={
            "CANO": self.account.cano, "ACNT_PRDT_CD": self.account.account_product_code,
            "INQR_STRT_DT": day, "INQR_END_DT": day, "SLL_BUY_DVSN_CD": side_code,
            "CCLD_DVSN": "00", "INQR_DVSN": "00", "INQR_DVSN_1": "",
            "INQR_DVSN_3": "01", "PDNO": stock_code, "ORD_GNO_BRNO": "",
            "ODNO": order_number, "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            "EXCG_ID_DVSN_CD": "KRX",
        })
        return tuple(self._parse(row) for row in response.get("output1", ()))

    @staticmethod
    def resolve(*, records: tuple[DailyMaBrokerHistoryOrder, ...], expected_quantity: int,
                known_order_number: str = "") -> tuple[UnknownResolution, DailyMaBrokerHistoryOrder | None]:
        if known_order_number:
            records = tuple(row for row in records if row.order_number == known_order_number)
        else:
            records = tuple(row for row in records if row.order_quantity == expected_quantity)
        if len(records) != 1:
            return UnknownResolution.UNRESOLVED, None
        record = records[0]
        if record.rejected_quantity >= record.order_quantity:
            return UnknownResolution.REJECTED, record
        return UnknownResolution.ACCEPTED, record

    @staticmethod
    def _parse(row: dict) -> DailyMaBrokerHistoryOrder:
        def integer(name: str) -> int:
            return int(str(row.get(name, "0") or "0"))
        return DailyMaBrokerHistoryOrder(
            str(row.get("ord_dt", "")), str(row.get("odno", "")), str(row.get("ord_gno_brno", "")),
            str(row.get("pdno", "")), "BUY" if str(row.get("sll_buy_dvsn_cd", "")) == "02" else "SELL",
            integer("ord_qty"), Decimal(str(row.get("avg_prvs", "0") or "0")), integer("tot_ccld_qty"),
            integer("rmn_qty"), integer("rjct_qty"), str(row.get("cncl_yn", "")).upper() == "Y",
            str(row.get("ord_tmd", "")),
        )
