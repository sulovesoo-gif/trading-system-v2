"""Planning safety only; broker send is always false in 7A."""
from __future__ import annotations
from datetime import datetime,timedelta
from .contracts import AccountPolicy,SafetyResult
class LiveOrderSafetyGate:
 def __init__(self,*,stale_after:timedelta=timedelta(minutes=2)):self.stale_after=stale_after
 def check(self,*,global_trade_yn:str,live_enabled:bool,intent_status:str,data_quality_status:str,now:datetime,target:datetime,available:float,policy:AccountPolicy,execution_stock_code:str,whitelist:set[str],side:str,has_real_position:bool=False,is_exit:bool=False)->SafetyResult:
  if not live_enabled:return SafetyResult(False,False,'LIVE_DISABLED_BLOCKED')
  if intent_status!='CREATED':return SafetyResult(False,False,'INTENT_NOT_CREATED')
  if data_quality_status not in {'PASS','LEGITIMATE_NO_BAR'}:return SafetyResult(False,False,'DATA_QUALITY_BLOCKED')
  if now-target>self.stale_after:return SafetyResult(False,False,'STALE_INTENT_BLOCKED')
  if execution_stock_code not in whitelist:return SafetyResult(False,False,'EXECUTION_STOCK_BLOCKED')
  if side not in {'BUY','SELL'}:return SafetyResult(False,False,'SIDE_BLOCKED')
  if is_exit and not has_real_position:return SafetyResult(False,False,'POSITION_REQUIRED')
  if not is_exit and available<=0:return SafetyResult(False,False,'CAPITAL_INSUFFICIENT')
  if policy.spendable_pool<policy.allocated_strategy_pool:return SafetyResult(False,False,'PROTECTED_RESERVE_BLOCKED')
  return SafetyResult(True,global_trade_yn=='Y','PLANNING_ALLOWED',{'broker_send_eligible':global_trade_yn=='Y'})
