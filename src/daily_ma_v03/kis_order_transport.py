"""Daily MA cash-order transport, deliberately separate from the 7C contract.

The payload is the KIS domestic cash-order contract in the supplied
``주식주문(현금)[v1_국내주식-001]`` specification: KRX market order
(``ORD_DVSN=01``, ``ORD_UNPR=0``), with a runtime-determined positive quantity.
This class makes one POST at most; callers must recover UNKNOWN through a
separate, authoritative order-history adapter and must never resend.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from src.broker.contracts import BrokerOrder
from src.collector.raw.kis_client import KISClient, KISClientError
from src.collector.raw.kis_order_account import KISOrderAccount

from .send_authorization import DailyMaSendProfile


LIVE_CASH_BUY_TR_ID = "TTTC0012U"
LIVE_CASH_SELL_TR_ID = "TTTC0011U"


class DailyMaKISOrderTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class DailyMaKISOrderTransportConfig:
    account_number: str
    account_product_code: str
    whitelist: frozenset[str]
    custtype: str = "P"

    @classmethod
    def from_environment(cls, *, whitelist: frozenset[str]) -> "DailyMaKISOrderTransportConfig":
        account = KISOrderAccount.from_environment()
        return cls(account.cano, account.account_product_code, whitelist, account.custtype)


class DailyMaKISOrderTransport:
    order_cash_path = "/uapi/domestic-stock/v1/trading/order-cash"

    def __init__(self, *, client: KISClient, config: DailyMaKISOrderTransportConfig) -> None:
        self._client, self._config = client, config
        self.actual_post_send_count = 0
        self.audit: list[dict[str, str]] = []

    def submit_once(self, order: BrokerOrder, *, profile: DailyMaSendProfile) -> Mapping[str, object]:
        profile.require_enabled()
        self._validate(order)
        payload = {
            "CANO": self._config.account_number,
            "ACNT_PRDT_CD": self._config.account_product_code,
            "PDNO": order.execution_stock_code,
            "ORD_DVSN": "01",
            "ORD_QTY": str(order.quantity),
            "ORD_UNPR": "0",
            "EXCG_ID_DVSN_CD": "KRX",
        }
        if order.side == "SELL":
            payload["SLL_TYPE"] = "01"
        tr_id = LIVE_CASH_BUY_TR_ID if order.side == "BUY" else LIVE_CASH_SELL_TR_ID
        self.audit.append({"request_key": order.client_order_key, "side": order.side,
                           "quantity": str(order.quantity), "response_classification": "POST_ATTEMPTED"})
        try:
            self.actual_post_send_count += 1
            response = self._client.post_once(path=self.order_cash_path, tr_id=tr_id, payload=payload,
                                              custtype=self._config.custtype)
        except KISClientError as error:
            self.audit[-1]["response_classification"] = "TRANSPORT_UNKNOWN"
            raise TimeoutError("DAILY_MA_KIS_SUBMIT_UNKNOWN") from error
        self.audit[-1]["response_classification"] = "ACK_ACCEPTED" if response.get("rt_cd") == "0" else "ACK_REJECTED"
        return response

    def _validate(self, order: BrokerOrder) -> None:
        if order.execution_stock_code not in self._config.whitelist:
            raise DailyMaKISOrderTransportError("DAILY_MA_EXECUTION_PRODUCT_NOT_ALLOWED")
        if order.side not in {"BUY", "SELL"} or order.quantity <= 0:
            raise DailyMaKISOrderTransportError("DAILY_MA_INVALID_ORDER")
        if order.payload.get("order_policy") != "DAILY_MA_KRX_MARKET":
            raise DailyMaKISOrderTransportError("DAILY_MA_ORDER_POLICY_REQUIRED")
        if self._config.custtype != "P":
            raise DailyMaKISOrderTransportError("DAILY_MA_PERSONAL_ACCOUNT_REQUIRED")
