import unittest
from decimal import Decimal
from src.daily_ma_v03.risk import RiskState,next_risk_state
class ThreeStrikeTest(unittest.TestCase):
 def test_loss_suspend_and_win_reset(self):
  s=RiskState()
  for _ in range(3):s=next_risk_state(s,Decimal('-1'))
  self.assertEqual((s.status,s.streak),('THREE_STRIKE_SUSPENDED',3))
  self.assertEqual(next_risk_state(s,Decimal('0')),s)
  self.assertEqual(next_risk_state(s,Decimal('1')),RiskState('ENABLED',0))
