"""Capital and order-planning contracts.  These are not broker orders."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

class CapitalEventType(str, Enum):
    INITIAL_ALLOCATION="INITIAL_ALLOCATION"; RESERVE="RESERVE"; RELEASE="RELEASE"; REALIZED_PNL="REALIZED_PNL"; FEE="FEE"; TAX="TAX"; ADJUSTMENT="ADJUSTMENT"
class OrderRequestStatus(str, Enum): PLANNED="PLANNED"; BLOCKED="BLOCKED"; CANCELLED_BEFORE_SEND="CANCELLED_BEFORE_SEND"; READY_FOR_BROKER="READY_FOR_BROKER"
def order_key(*, intent_id:str, strategy_instance_id:str, side:str, execution_stock_code:str)->str:
    return sha256(f"{intent_id}|{strategy_instance_id}|{side}|{execution_stock_code}".encode()).hexdigest()
@dataclass(frozen=True)
class CapitalAccount:
    strategy_instance_id:str; initial_capital:float; realized_net_pnl:float=0; reserved_amount:float=0; updated_at:datetime=field(default_factory=datetime.now)
    @property
    def available_capital(self)->float: return self.initial_capital+self.realized_net_pnl-self.reserved_amount
@dataclass(frozen=True)
class CapitalEvent:
    strategy_instance_id:str; event_type:CapitalEventType; amount:float; balance_after:float; reason:str; related_intent_id:str|None=None; related_order_request_id:str|None=None; occurred_at:datetime=field(default_factory=datetime.now)
@dataclass(frozen=True)
class OrderRequest:
    order_request_id:str; idempotency_key:str; strategy_instance_id:str; source_intent_id:str; source_decision_id:str; execution_stock_code:str; side:str; requested_notional:float; requested_quantity:int|None; reference_price:float|None; order_type:str; execution_target_time:datetime; strategy_capital_before:float; reserved_capital:float; safety_status:str; status:OrderRequestStatus; reason:str; detail:Mapping[str,Any]=field(default_factory=dict); created_at:datetime=field(default_factory=datetime.now)
    @staticmethod
    def build(**kwargs:Any)->"OrderRequest":
        key=order_key(intent_id=kwargs['source_intent_id'],strategy_instance_id=kwargs['strategy_instance_id'],side=kwargs['side'],execution_stock_code=kwargs['execution_stock_code'])
        return OrderRequest(str(uuid5(NAMESPACE_URL,"live-order-request|"+key)),key,**kwargs)
@dataclass(frozen=True)
class AccountPolicy:
    account_cash:float; protected_reserve:float; allocated_strategy_pool:float
    @property
    def spendable_pool(self)->float:return max(0.,self.account_cash-self.protected_reserve)
@dataclass(frozen=True)
class SafetyResult:
    allowed:bool; broker_send_eligible:bool; reason:str; detail:Mapping[str,Any]=field(default_factory=dict)
