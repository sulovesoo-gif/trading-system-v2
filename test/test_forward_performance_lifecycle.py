from decimal import Decimal
from src.forward.performance_runtime import ForwardPerformanceLifecycle
class Store:
 def __init__(self):self.calls=[]
 def save(self,*args,**kwargs):self.calls.append((args,kwargs))
def test_closed_forward_trade_persists_tracker_snapshot():
 store=Store(); lifecycle=ForwardPerformanceLifecycle(path_id='FORWARD_X',store=store,normalized_initial_capital=Decimal('1000'))
 result=lifecycle.close_trade(actual_1share_pnl=10,costs=2,entry_notional=100,normalized_trade_return='.1',mfe='.2',mae='-.01',fill_rate=1,miss_rate=0)
 assert result.cost_adjusted_actual_pnl==Decimal('8') and store.calls[0][0][0]=='FORWARD_X'
