from dataclasses import dataclass
from datetime import datetime,time
@dataclass(frozen=True)
class SmokeConfig:
 phase:str;active_product:str|None=None;active_strategy_instance:str|None=None;allowed_start:time|None=None;allowed_end:time|None=None;kill_switch_enabled:bool=False
@dataclass(frozen=True)
class SmokeRequest: product:str;strategy_instance_id:str;side:str;quantity:int;at:datetime;outstanding:int;daily_submit_count:int;actual_position_quantity:int=0
class SmokeGate:
 whitelist={'0193W0':('KODEX Samsung Electronics single-stock leverage','ETF'),'0193L0':('PLUS Samsung Electronics single-stock inverse 2X','ETF'),'0197X0':('SOL SK hynix single-stock inverse 2X','ETF')}
 def validate(self,c:SmokeConfig,r:SmokeRequest):
  if not c.kill_switch_enabled:return False,'KILL_SWITCH_BLOCKED'
  if not c.active_product or not c.active_strategy_instance:return False,'PHASE_NOT_APPROVED'
  if r.product!=c.active_product or r.strategy_instance_id!=c.active_strategy_instance:return False,'WHITELIST_OR_ATTRIBUTION_BLOCKED'
  if r.product not in self.whitelist:return False,'PRODUCT_BLOCKED'
  if r.quantity!=1:return False,'QTY_MUST_BE_ONE'
  if r.daily_submit_count>=1:return False,'DAILY_SUBMIT_LIMIT'
  if r.outstanding>=1:return False,'OUTSTANDING_LIMIT'
  if c.allowed_start is None or c.allowed_end is None or not(c.allowed_start<=r.at.time()<=c.allowed_end):return False,'TIME_WINDOW_BLOCKED'
  if c.phase=='7C-1' and r.side!='BUY':return False,'PHASE_SIDE_BLOCKED'
  if c.phase=='7C-2' and not(r.side=='SELL' and r.actual_position_quantity==1):return False,'POSITION_REQUIRED'
  return False,'NO_SUBMIT_IMPLEMENTED'
