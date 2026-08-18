"""Forward closed-trade lifecycle -> durable performance snapshot."""
from __future__ import annotations
from decimal import Decimal
from .contracts import ForwardPerformanceTracker

class ForwardPerformanceLifecycle:
 def __init__(self,*,path_id,store,normalized_initial_capital):
  self.path_id,self.store=path_id,store;self.tracker=ForwardPerformanceTracker(normalized_initial_capital=normalized_initial_capital)
 def close_trade(self,*,actual_1share_pnl,costs,entry_notional,normalized_trade_return,**metrics):
  performance=self.tracker.record_closed_trade(actual_1share_pnl=Decimal(str(actual_1share_pnl)),costs=Decimal(str(costs)),entry_notional=Decimal(str(entry_notional)),normalized_trade_return=Decimal(str(normalized_trade_return)))
  self.store.save(self.path_id,performance,**metrics)
  return performance
