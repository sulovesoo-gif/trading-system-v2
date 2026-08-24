"""Read-only KIS domestic-stock orderable-cash lookup.

The endpoint is an account inquiry (GET), never an order transport.  Its
``ord_psbl_cash`` is treated as broker-authoritative orderable cash; callers
must not subtract application reservations again.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class KISOrderableCash:
    amount: Decimal
    includes_pending_order_reservations: bool = True


class KISBrokerAvailableCashLookup:
    path = "/uapi/domestic-stock/v1/trading/inquire-psbl-order"
    tr_id = "TTTC8908R"

    def __init__(self, *, client, account) -> None:
        self.client, self.account = client, account

    def orderable_cash(self, *, stock_code: str, order_price: Decimal, order_division: str = "00") -> KISOrderableCash:
        payload = self.client.get(path=self.path, tr_id=self.tr_id, params={
            "CANO": self.account.cano,
            "ACNT_PRDT_CD": self.account.account_product_code,
            "PDNO": stock_code,
            "ORD_UNPR": str(int(order_price)),
            "ORD_DVSN": order_division,
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        })
        output = payload.get("output", {})
        raw = str(output.get("ord_psbl_cash", "")).strip()
        if not raw:
            raise ValueError("KIS orderable-cash response omitted ord_psbl_cash")
        return KISOrderableCash(amount=Decimal(raw), includes_pending_order_reservations=True)
