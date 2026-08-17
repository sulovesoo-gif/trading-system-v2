from __future__ import annotations
from dataclasses import dataclass,field
from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Any,Mapping
from uuid import uuid5,NAMESPACE_URL
class BrokerMode(str,Enum): NO_SEND='NO_SEND';LIVE_SEND='LIVE_SEND'
class BrokerOrderStatus(str,Enum): PREPARED='PREPARED';NO_SEND_VALIDATED='NO_SEND_VALIDATED';SUBMITTING='SUBMITTING';ACCEPTED='ACCEPTED';REJECTED='REJECTED';PARTIALLY_FILLED='PARTIALLY_FILLED';FILLED='FILLED';CANCEL_REQUESTED='CANCEL_REQUESTED';CANCELLED='CANCELLED';UNKNOWN_BROKER_STATE='UNKNOWN_BROKER_STATE'
def client_key(request_id:str)->str:return sha256(('broker|'+request_id).encode()).hexdigest()
@dataclass(frozen=True)
class BrokerOrder:
 broker_order_id:str;order_request_id:str;strategy_instance_id:str;execution_stock_code:str;side:str;quantity:int;client_order_key:str;status:BrokerOrderStatus;payload:Mapping[str,Any];broker_order_number:str|None=None;created_at:datetime=field(default_factory=datetime.now)
@dataclass(frozen=True)
class BrokerFill:
 fill_id:str;broker_order_id:str;order_request_id:str;strategy_instance_id:str;execution_stock_code:str;side:str;fill_quantity:int;fill_price:float;gross_amount:float;fee:float;tax:float;other_cost:float;filled_at:datetime;broker_trade_id:str;idempotency_key:str;raw_broker_detail:Mapping[str,Any]
 @staticmethod
 def build(**k):
  key=sha256(f"{k['broker_order_id']}|{k['broker_trade_id']}".encode()).hexdigest();return BrokerFill(str(uuid5(NAMESPACE_URL,'fill|'+key)),idempotency_key=key,**k)
