import unittest
from src.daily_ma_v03.actual_submit import *
class T:
 def __init__(self,timeout=False):self.n=0;self.timeout=timeout;self.lookups=0
 def submit_once(self,k):self.n+=1; 
 def lookup(self,k):self.lookups+=1;return {'key':k}
class ActualSubmitTest(unittest.TestCase):
 def test_locked_and_unknown_never_resend(self):
  t=T();r=DailyMaActualSubmitRuntime(store=InMemoryDailyMaSubmitStore(),transport=t)
  self.assertEqual(r.submit('a')[1],'SEND_LOCKED');self.assertEqual(t.n,0)
  class Timeout(T):
   def submit_once(self,k):self.n+=1;raise TimeoutError()
  t=Timeout();r=DailyMaActualSubmitRuntime(store=InMemoryDailyMaSubmitStore(),transport=t,send_enabled=True)
  self.assertEqual(r.submit('a')[1],'UNKNOWN_BROKER_STATE');self.assertEqual(r.submit('a')[1],'RESEND_FORBIDDEN');self.assertEqual(t.n,1);self.assertEqual(r.recover('a')['key'],'a')
