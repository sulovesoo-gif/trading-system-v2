"""Daily MA actual-submit state machine. Disabled unless explicitly armed."""
from dataclasses import dataclass
from enum import Enum

from .kis_order_history import UnknownResolution

class SubmitState(str,Enum): PREPARED='PREPARED'; ACCEPTED='ACCEPTED'; REJECTED='REJECTED'; UNKNOWN_BROKER_STATE='UNKNOWN_BROKER_STATE'
@dataclass
class SubmitRecord:
 request_key:str
 state:SubmitState=SubmitState.PREPARED
 broker_order_number:str|None=None
 broker_response_code:str|None=None
 broker_response_message:str|None=None
 broker_response:dict|None=None
class InMemoryDailyMaSubmitStore:
 def __init__(self):self.rows={}
 def get_or_create(self,key):return self.rows.setdefault(key,SubmitRecord(key))
class DailyMaActualSubmitRuntime:
 """One request key gets at most one submit attempt; UNKNOWN is lookup-only."""
 def __init__(self,*,store,transport,send_enabled=False):self.store,self.transport,self.send_enabled=store,transport,send_enabled
 def submit(self,key):
  r=self.store.get_or_create(key)
  if not self.send_enabled:return r,'SEND_LOCKED'
  if r.state is not SubmitState.PREPARED:return r,'RESEND_FORBIDDEN'
  try: raw=self.transport.submit_once(key)
  except TimeoutError:r.state=SubmitState.UNKNOWN_BROKER_STATE;return r,'UNKNOWN_BROKER_STATE'
  if raw.get('rt_cd')=='0':r.state=SubmitState.ACCEPTED;r.broker_order_number=raw.get('odno');return r,'ACK'
  r.state=SubmitState.REJECTED;return r,'REJECTED'
 def recover(self,key):
  r=self.store.get_or_create(key)
  return self.transport.lookup(key) if r.state is SubmitState.UNKNOWN_BROKER_STATE else None


class DailyMaBrokerSubmitRuntime:
 """One Daily MA broker order may enter POST only once.

 ``transport`` is the Daily MA-specific KIS transport.  It has no 7C permit
 dependency; the profile is the independent Daily MA authorization boundary.
 """
 def __init__(self,*,store,transport,profile):self.store,self.transport,self.profile=store,transport,profile
 def submit(self,order):
  r=self.store.get_or_create(order.client_order_key)
  if r.state is not SubmitState.PREPARED:return r,'RESEND_FORBIDDEN'
  try: raw=self.transport.submit_once(order,profile=self.profile)
  except PermissionError:return r,'SEND_LOCKED'
  except TimeoutError:r.state=SubmitState.UNKNOWN_BROKER_STATE;return r,'UNKNOWN_BROKER_STATE'
  if raw.get('rt_cd')=='0':r.state=SubmitState.ACCEPTED;r.broker_order_number=str(raw.get('output',{}).get('ODNO') or '');return r,'ACK'
  r.state=SubmitState.REJECTED
  r.broker_response=dict(raw)
  r.broker_response_code=str(raw.get('msg_cd') or raw.get('rt_cd') or '')
  r.broker_response_message=str(raw.get('msg1') or '')
  return r,'REJECTED'

 def recover_unknown(self,*,order,history_lookup,order_date):
  """Read-only recovery.  UNRESOLVED deliberately leaves resend forbidden."""
  r=self.store.get_or_create(order.client_order_key)
  if r.state is not SubmitState.UNKNOWN_BROKER_STATE:return r,'NOT_UNKNOWN'
  records=history_lookup.orders_for_day(order_date=order_date,stock_code=order.execution_stock_code,side=order.side,order_number=r.broker_order_number or '')
  resolution,match=history_lookup.resolve(records=records,expected_quantity=order.quantity,known_order_number=r.broker_order_number or '')
  if resolution is UnknownResolution.ACCEPTED:
   r.state=SubmitState.ACCEPTED;r.broker_order_number=match.order_number;return r,'RECOVERED_ACK'
  if resolution is UnknownResolution.REJECTED:
   r.state=SubmitState.REJECTED;r.broker_order_number=match.order_number;return r,'RECOVERED_REJECTED'
  return r,'RECOVERY_UNRESOLVED'


class DailyMaDurableSubmitService:
 """Claim durably first, then make at most one transport attempt."""
 def __init__(self,*,store,runtime):self.store,self.runtime=store,runtime
 def submit_request(self,request_key):
  order=self.store.claim(request_key=request_key)
  if order is None:return None,'RESEND_FORBIDDEN'
  record,status=self.runtime.submit(order)
  if status=='ACK':self.store.acknowledge(order=order,raw={'output':{'ODNO':record.broker_order_number}})
  elif status=='UNKNOWN_BROKER_STATE':self.store.mark_unknown(order=order)
  elif status=='REJECTED' and hasattr(self.store,'reject'):
   self.store.reject(order=order,raw=record.broker_response or {
    'rt_cd':record.broker_response_code,'msg1':record.broker_response_message})
  return record,status
