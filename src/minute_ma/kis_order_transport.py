"""Minute-MA KRX market-order transport; independent from Daily and 7C profiles."""
from dataclasses import dataclass
from typing import Mapping
from src.collector.raw.kis_client import KISClient, KISClientError
from src.collector.raw.kis_order_account import KISOrderAccount
from src.broker.contracts import BrokerOrder
from .send_authorization import MinuteMaSendProfile

@dataclass(frozen=True)
class MinuteMaKISOrderTransportConfig:
    account_number: str
    account_product_code: str
    whitelist: frozenset[str]
    custtype: str = "P"
    @classmethod
    def from_environment(cls, *, whitelist):
        account=KISOrderAccount.from_environment()
        return cls(account.cano,account.account_product_code,whitelist,account.custtype)

class MinuteMaKISOrderTransport:
    path="/uapi/domestic-stock/v1/trading/order-cash"
    def __init__(self,*,client:KISClient,config:MinuteMaKISOrderTransportConfig):
        self.client,self.config=client,config
        self.actual_post_send_count=0
    def submit_once(self,order:BrokerOrder,*,profile:MinuteMaSendProfile)->Mapping[str,object]:
        profile.require_enabled()
        if order.execution_stock_code not in self.config.whitelist:
            raise ValueError("MINUTE_MA_EXECUTION_PRODUCT_NOT_ALLOWED")
        if order.side not in {"BUY","SELL"} or order.quantity<=0:
            raise ValueError("MINUTE_MA_INVALID_ORDER")
        if order.payload.get("order_policy")!="MINUTE_MA_KRX_MARKET":
            raise ValueError("MINUTE_MA_ORDER_POLICY_REQUIRED")
        payload={"CANO":self.config.account_number,"ACNT_PRDT_CD":self.config.account_product_code,
                 "PDNO":order.execution_stock_code,"ORD_DVSN":"01","ORD_QTY":str(order.quantity),
                 "ORD_UNPR":"0","EXCG_ID_DVSN_CD":"KRX"}
        if order.side=="SELL":payload["SLL_TYPE"]="01"
        try:
            self.actual_post_send_count+=1
            return self.client.post_once(path=self.path,tr_id="TTTC0012U" if order.side=="BUY" else "TTTC0011U",
                                         payload=payload,custtype=self.config.custtype)
        except KISClientError as error:
            raise TimeoutError("MINUTE_MA_KIS_SUBMIT_UNKNOWN") from error
