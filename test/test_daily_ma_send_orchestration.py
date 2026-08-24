import unittest
from src.daily_ma_v03.send_orchestration import DailyMaSendOrchestrator

class SendOrchestrationTest(unittest.TestCase):
 def test_fresh_or_restarted_orchestrator_submits_one_durable_request_once(self):
  class Store:
   def __init__(self): self.claimed=False; self.acks=0
   def claim(self,request_key):
    if self.claimed:return None
    self.claimed=True;return type('O',(),{'client_order_key':request_key})()
   def acknowledge(self,**_):self.acks+=1
   def mark_unknown(self,**_):raise AssertionError()
  class Runtime:
   def __init__(self):self.posts=0
   def submit(self,o):self.posts+=1;return type('R',(),{'broker_order_number':'1'})(),'ACK'
  store=Store(); runtime=Runtime()
  self.assertEqual(DailyMaSendOrchestrator(submit_store=store,submit_runtime=runtime).process_request('K')[1],'ACK')
  self.assertEqual(DailyMaSendOrchestrator(submit_store=store,submit_runtime=runtime).process_request('K')[1],'RESEND_FORBIDDEN')
  self.assertEqual((runtime.posts,store.acks),(1,1))
