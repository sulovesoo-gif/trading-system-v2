"""KIS cash-order POST transport injected only behind the phase-7C broker adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from src.broker.contracts import BrokerOrder
from src.smoke_send.authorization import validate_transport_permit

from .kis_client import KISClient, KISClientError
from .kis_order_account import KISOrderAccount


class KISOrderTransportError(RuntimeError):
    pass


LIVE_CASH_BUY_TR_ID = "TTTC0012U"
LIVE_CASH_SELL_TR_ID = "TTTC0011U"


@dataclass(frozen=True)
class KISOrderTransportConfig:
    """Non-secret account/TR configuration; no defaults or implicit account."""

    account_number: str
    account_product_code: str
    buy_tr_id: str
    sell_tr_id: str
    whitelist: frozenset[str]
    custtype: str = "P"

    @classmethod
    def from_environment(cls, *, whitelist: frozenset[str]) -> "KISOrderTransportConfig":
        account = KISOrderAccount.from_environment()
        return cls(
            account_number=account.cano,
            account_product_code=account.account_product_code,
            buy_tr_id=LIVE_CASH_BUY_TR_ID,
            sell_tr_id=LIVE_CASH_SELL_TR_ID,
            whitelist=whitelist,
            custtype=account.custtype,
        )


class KISOrderPostTransport:
    """One pre-gated BrokerOrder -> one KIS cash-order POST attempt.

    Strategy, approval consumption, retry and recovery are deliberately outside
    this class. It accepts only a phase-7C one-share broker order.
    """

    order_cash_path = "/uapi/domestic-stock/v1/trading/order-cash"

    def __init__(self, *, client: KISClient, config: KISOrderTransportConfig) -> None:
        self._client = client
        self._config = config
        self.invocation_count = 0
        self.actual_post_send_count = 0
        self.audit: list[dict[str, str]] = []

    def submit_once(self, order: BrokerOrder, *, permit: object = None) -> Mapping[str, object]:
        self._validate(order, permit)
        self.invocation_count += 1
        payload = {
            "CANO": self._config.account_number,
            "ACNT_PRDT_CD": self._config.account_product_code,
            "PDNO": order.execution_stock_code,
            "ORD_DVSN": str(order.payload["ORD_DVSN"]),
            "ORD_QTY": str(order.quantity),
            "ORD_UNPR": str(order.payload["ORD_UNPR"]),
            "EXCG_ID_DVSN_CD": str(order.payload["EXCG_ID_DVSN_CD"]),
        }
        tr_id = self._config.buy_tr_id if order.side == "BUY" else self._config.sell_tr_id
        self.audit.append({
            "approval_id": order.order_request_id,
            "idempotency_key": order.client_order_key,
            "stock_code": order.execution_stock_code,
            "side": order.side,
            "quantity": str(order.quantity),
            "response_classification": "POST_ATTEMPTED",
        })
        try:
            self.actual_post_send_count += 1
            response = self._client.post_once(
                path=self.order_cash_path, tr_id=tr_id, payload=payload,
                custtype=self._config.custtype,
            )
        except KISClientError as error:
            self.audit[-1]["response_classification"] = "TRANSPORT_ERROR"
            raise TimeoutError("KIS POST response unavailable") from error
        self.audit[-1]["response_classification"] = "ACK_ACCEPTED" if response.get("rt_cd") == "0" else "ACK_REJECTED"
        return response

    def lookup(self, _idempotency_key: str):
        raise KISOrderTransportError("broker lookup is a separate recovery adapter")

    def _validate(self, order: BrokerOrder, permit: object) -> None:
        validate_transport_permit(permit, order)
        if order.payload.get("phase") != "7C-1":
            raise KISOrderTransportError("phase-7C broker order required")
        if order.execution_stock_code not in self._config.whitelist:
            raise KISOrderTransportError("execution stock is not whitelisted")
        if order.side not in {"BUY", "SELL"} or order.quantity != 1:
            raise KISOrderTransportError("7C transport requires an approved one-share order")
        if not all((self._config.account_number, self._config.account_product_code, self._config.buy_tr_id, self._config.sell_tr_id)):
            raise KISOrderTransportError("KIS account/TR configuration is incomplete")
        if len(self._config.account_number) != 8 or not self._config.account_number.isdigit() or len(self._config.account_product_code) != 2:
            raise KISOrderTransportError("KIS account configuration must come from an explicit valid resolver value")
        if self._config.custtype != "P":
            raise KISOrderTransportError("7C transport requires personal custtype P")
        if (self._config.buy_tr_id, self._config.sell_tr_id) != (LIVE_CASH_BUY_TR_ID, LIVE_CASH_SELL_TR_ID):
            raise KISOrderTransportError("KIS live cash TR IDs do not match the official contract")
        if (order.payload.get("EXCG_ID_DVSN_CD"), order.payload.get("ORD_DVSN"), order.payload.get("ORD_UNPR")) != ("KRX", "15", "0"):
            raise KISOrderTransportError("7C transport requires KRX IOC-best order at zero price")
