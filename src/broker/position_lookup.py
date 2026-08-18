"""Read-only KIS domestic-stock position lookup; contains no order endpoint."""

from __future__ import annotations


class KISBrokerPositionLookup:
    path = "/uapi/domestic-stock/v1/trading/inquire-balance"
    tr_id = "TTTC8434R"

    def __init__(self, *, client, account) -> None:
        self.client, self.account = client, account

    def net_quantities(self) -> dict[str, int]:
        payload = self.client.get(path=self.path, tr_id=self.tr_id, params={
            "CANO": self.account.cano, "ACNT_PRDT_CD": self.account.account_product_code,
            "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        })
        result: dict[str, int] = {}
        for row in payload.get("output1", []):
            code, quantity = str(row.get("pdno", "")).strip(), int(str(row.get("hldg_qty", "0") or "0"))
            if code:
                result[code] = quantity
        return result
