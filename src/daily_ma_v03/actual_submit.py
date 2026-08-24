"""Daily MA actual-submit state machine. Disabled unless explicitly armed."""
from dataclasses import dataclass
from enum import Enum

class SubmitState(str,Enum): PREPARED='PREPARED'; ACCEPTED='ACCEPTED'; REJECTED='REJECTED'; UNKNOWN_BROKER_STATE='UNKNOWN_BROKER_STATE'
@dataclass
class SubmitRecord: request_key:str; state:SubmitState=SubmitState.PREPARED; broker_order_number:str|None=None
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
  r.state=SubmitState.REJECTED;return r,'REJECTED'
