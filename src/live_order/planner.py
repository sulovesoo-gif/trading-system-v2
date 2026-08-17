"""Live Intent -> capital reserve -> no-send internal OrderRequest."""
from __future__ import annotations
from datetime import datetime
from src.live_intent.contracts import IntentType,LiveIntent
from .contracts import AccountPolicy,OrderRequest,OrderRequestStatus,SafetyResult,order_key
from .persistence import InMemoryOrderPlanningStore
from .safety import LiveOrderSafetyGate
class OrderPlanner:
 def __init__(self,*,store,gate,policy,whitelist,fee_rate=.000146527):self.store,self.gate,self.policy,self.whitelist,self.fee_rate=store,gate,policy,set(whitelist),fee_rate
 def plan(self,*,intent:LiveIntent,reference_price:float|None,now:datetime,live_enabled:bool,global_trade_yn:str,has_real_position:bool=False):
  account=self.store.account(intent.strategy_instance_id);is_exit=intent.intent_type==IntentType.EXIT_INTENT;side='SELL' if is_exit else 'BUY'
  existing=self.store.request_by_key(order_key(intent_id=intent.intent_id,strategy_instance_id=intent.strategy_instance_id,side=side,execution_stock_code=intent.execution_stock_code))
  if existing is not None:return existing,SafetyResult(True,False,'ORDER_REQUEST_DUPLICATE_SUPPRESSED')
  safe=self.gate.check(global_trade_yn=global_trade_yn,live_enabled=live_enabled,intent_status=intent.status.value,data_quality_status=intent.data_quality_status,now=now,target=intent.execution_target_time,available=account.available_capital,policy=self.policy,execution_stock_code=intent.execution_stock_code,whitelist=self.whitelist,side=side,has_real_position=has_real_position,is_exit=is_exit)
  if not safe.allowed:return None,safe
  if is_exit:return None,SafetyResult(False,False,'POSITION_REQUIRED')
  if reference_price is None or reference_price<=0:return None,SafetyResult(False,False,'REFERENCE_PRICE_BLOCKED')
  quantity=int(account.available_capital//(reference_price*(1+self.fee_rate)));notional=quantity*reference_price;reserve=notional*(1+self.fee_rate)
  if quantity<=0:return None,SafetyResult(False,False,'QUANTITY_ZERO_BLOCK')
  request=OrderRequest.build(strategy_instance_id=intent.strategy_instance_id,source_intent_id=intent.intent_id,source_decision_id=intent.source_decision_id,execution_stock_code=intent.execution_stock_code,side=side,requested_notional=notional,requested_quantity=quantity,reference_price=reference_price,order_type='MARKET_REFERENCE_ONLY',execution_target_time=intent.execution_target_time,strategy_capital_before=account.available_capital,reserved_capital=reserve,safety_status=safe.reason,status=OrderRequestStatus.PLANNED,reason='ENTRY_PLANNED',detail={'broker_send_eligible':False})
  return self.store.reserve_and_create(request,reserve)[0],safe
