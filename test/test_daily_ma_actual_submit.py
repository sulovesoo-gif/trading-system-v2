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
 def test_durable_service_claims_once_then_maps_ack(self):
  class Store:
   def __init__(self):self.claims=0;self.acked=0;self.unknown=0
   def claim(self,request_key):
    self.claims+=1
    return None if self.claims>1 else type('Order',(),{'client_order_key':request_key})()
   def acknowledge(self,**_):self.acked+=1
   def mark_unknown(self,**_):self.unknown+=1
  class Runtime:
   def submit(self,order):return type('Record',(),{'broker_order_number':'0001'})(),'ACK'
  store=Store(); service=DailyMaDurableSubmitService(store=store,runtime=Runtime())
  self.assertEqual(service.submit_request('request')[1],'ACK');self.assertEqual(store.acked,1);self.assertEqual(service.submit_request('request')[1],'RESEND_FORBIDDEN')
