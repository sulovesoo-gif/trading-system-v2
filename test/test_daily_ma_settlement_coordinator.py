from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
import unittest
from src.daily_ma_v03.broker_cost_allocation import CostAllocation
from src.daily_ma_v03.settlement_coordinator import DailyMaSettlementCoordinator
class T(unittest.TestCase):
 def test_restart_exactly_once(self):
  row=SimpleNamespace(live_trade_id=1,strategy_id='DS1',capital_epoch_no=1,entry_filled_amount=Decimal('100'),exit_filled_amount=Decimal('110'),allocations=(CostAllocation(1,'BUY',Decimal('100'),buy_fee=Decimal('2')),CostAllocation(1,'SELL',Decimal('110'),sell_fee=Decimal('3'),sell_tax=Decimal('1'))))
  class R:
   def closed_cost_finalized_trades(self):return (row,)
  class C:
   def __init__(self):self.done=set()
   def apply_settlement(self,**k):
    if k['live_trade_id'] in self.done:return False
    self.done.add(k['live_trade_id']);return True
  c=C();x=lambda:DailyMaSettlementCoordinator(repository=R(),capital_store=c,clock=lambda:datetime(2026,1,1))
  self.assertEqual(x().settle_due(),1);self.assertEqual(x().settle_due(),0)
