from decimal import Decimal
import unittest
from src.analysis.strategy.multi_ma_performance import Portfolio
class PerformanceTest(unittest.TestCase):
 def test_leg_uses_whole_share_notional_only(self):
  p=Portfolio(Decimal('10000000')); p.enter('LONG',Decimal('1521000'),Decimal('1'),'SIGNAL_1')
  self.assertEqual(p.legs[0].quantity,6); self.assertEqual(p.legs[0].notional_amount,Decimal('9126000'))
 def test_partial_session_close_uses_actual_leg_weights_and_resets(self):
  p=Portfolio(Decimal('900')); p.enter('LONG',Decimal('100'),Decimal('0.333333333333'),'SIGNAL_1'); p.enter('LONG',Decimal('110'),Decimal('0.333333333333'),'SIGNAL_2')
  pnl, legs=p.close(Decimal('120')); self.assertGreater(pnl,0); self.assertEqual(len(legs),2); self.assertEqual(p.direction,'FLAT'); self.assertEqual(p.legs,[])
