from datetime import date
import unittest
from src.daily_ma_v03.runtime_loop import DailyMaActualRuntimeLoop
from src.daily_ma_v03.send_orchestration import DailyMaSendOrchestrator

class RuntimeLoopTest(unittest.TestCase):
 def test_restart_uses_durable_discovery_and_never_resubmits(self):
  class Store:
   def __init__(self):self.claimed=False
   def discover_ready_request_keys(self):return ('k',) if not self.claimed else ()
   def claim(self,request_key):
    if self.claimed:return None
    self.claimed=True;return type('O',(),{'client_order_key':request_key})()
   def acknowledge(self,**_):pass
   def mark_unknown(self,**_):pass
  class Runtime:
   def __init__(self):self.posts=0
   def submit(self,o):self.posts+=1;return type('R',(),{'broker_order_number':'x'})(),'ACK'
  class Poll:
   def __init__(self):self.n=0
   def poll_and_recover(self,**_):self.n+=1;return 'CHECKPOINTS'
  class Cost:
   def __init__(self):self.n=0
   def finalize_due(self,**_):self.n+=1;return 'PENDING'
  s=Store(); r=Runtime();p=Poll();c=Cost(); loop=lambda:DailyMaActualRuntimeLoop(request_repository=s,orchestrator=DailyMaSendOrchestrator(submit_store=s,submit_runtime=r),checkpoint_poller=p,cost_finalizer=c)
  self.assertEqual(loop().run_once(today=date(2026,8,25))['submitted'],(('k','ACK'),))
  self.assertEqual(loop().run_once(today=date(2026,8,25))['submitted'],())
  self.assertEqual((r.posts,p.n,c.n),(1,2,2))
