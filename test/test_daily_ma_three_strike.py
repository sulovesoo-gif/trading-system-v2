import unittest
from decimal import Decimal
from src.daily_ma_v03.risk import RiskState,next_risk_state
from src.daily_ma_v03.runtime import DailyMaPaperRuntime
class ThreeStrikeTest(unittest.TestCase):
 def test_loss_suspend_and_win_reset(self):
  s=RiskState()
  for _ in range(3):s=next_risk_state(s,Decimal('-1'))
  self.assertEqual((s.status,s.streak),('THREE_STRIKE_SUSPENDED',3))
  self.assertEqual(next_risk_state(s,Decimal('0')),s)
  self.assertEqual(next_risk_state(s,Decimal('1')),RiskState('ENABLED',0))
 def test_completed_paper_trade_is_forwarded_once_to_risk_store(self):
  class Repo:
   def completed_trade_return(self,_):return ('DS000103',-1.0)
  class Risk:
   def __init__(self):self.calls=[]
   def apply_completed_paper_trade(self,**kw):self.calls.append(kw)
  risk=Risk();runtime=DailyMaPaperRuntime(repository=Repo(),raw_provider=None,risk_store=risk)
  runtime._apply_risk(77)
  self.assertEqual(risk.calls,[{'paper_trade_id':77,'strategy_id':'DS000103','return_pct':-1.0}])
