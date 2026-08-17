import unittest
from datetime import datetime,time
from src.smoke_gate import SmokeConfig,SmokeRequest,SmokeGate
class T(unittest.TestCase):
 def test_defaults_and_all_non_one_or_wrong_direction_block(self):
  g=SmokeGate();r=SmokeRequest('0197X0','one','BUY',1,datetime(2026,8,1,10),0,0);self.assertEqual(g.validate(SmokeConfig('7C-1'),r)[1],'KILL_SWITCH_BLOCKED');c=SmokeConfig('7C-1','0197X0','one',time(9),time(15),True);self.assertEqual(g.validate(c,SmokeRequest('0197X0','one','BUY',2,datetime(2026,8,1,10),0,0))[1],'QTY_MUST_BE_ONE');self.assertEqual(g.validate(c,SmokeRequest('0197X0','one','SELL',1,datetime(2026,8,1,10),0,0))[1],'PHASE_SIDE_BLOCKED');self.assertEqual(g.validate(c,r)[1],'NO_SUBMIT_IMPLEMENTED')
if __name__=='__main__':unittest.main()
