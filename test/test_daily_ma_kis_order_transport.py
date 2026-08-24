import unittest
from datetime import datetime

from src.broker.contracts import BrokerOrder, BrokerOrderStatus
from src.daily_ma_v03.actual_submit import DailyMaBrokerSubmitRuntime, InMemoryDailyMaSubmitStore
from src.daily_ma_v03.kis_order_transport import DailyMaKISOrderTransport, DailyMaKISOrderTransportConfig
from src.daily_ma_v03.send_authorization import DailyMaSendProfile


class Client:
 def __init__(self,response=None,error=None):self.response=response or {'rt_cd':'0','output':{'ODNO':'masked'}};self.error=error;self.calls=[]
 def post_once(self,**kwargs):
  self.calls.append(kwargs)
  if self.error:raise self.error
  return self.response

def order(side='BUY',quantity=7):
 return BrokerOrder('broker','request','daily-ma','005930',side,quantity,'key',BrokerOrderStatus.SUBMITTING,
                    {'order_policy':'DAILY_MA_KRX_MARKET'},created_at=datetime(2026,8,25,15,19))

class DailyMaKisOrderTransportTest(unittest.TestCase):
 def transport(self,response=None,error=None):
  client=Client(response,error); return DailyMaKISOrderTransport(client=client,config=DailyMaKISOrderTransportConfig('12345678','01',frozenset({'005930'}))),client
 def test_market_contract_uses_runtime_quantity_and_daily_ma_profile(self):
  t,c=self.transport(); result=t.submit_once(order(),profile=DailyMaSendProfile(enabled=True)); self.assertEqual(result['rt_cd'],'0'); self.assertEqual(c.calls[0]['tr_id'],'TTTC0012U'); self.assertEqual(c.calls[0]['payload']['ORD_DVSN'],'01'); self.assertEqual(c.calls[0]['payload']['ORD_UNPR'],'0'); self.assertEqual(c.calls[0]['payload']['ORD_QTY'],'7'); self.assertEqual(c.calls[0]['payload']['EXCG_ID_DVSN_CD'],'KRX')
 def test_disabled_profile_never_calls_post(self):
  t,c=self.transport(); runtime=DailyMaBrokerSubmitRuntime(store=InMemoryDailyMaSubmitStore(),transport=t,profile=DailyMaSendProfile(enabled=False)); _,status=runtime.submit(order()); self.assertEqual(status,'SEND_LOCKED'); self.assertEqual(c.calls,[]); self.assertEqual(t.actual_post_send_count,0)
 def test_unknown_is_lookup_only_and_never_reposts(self):
  class TimeoutClient(Client):
   def post_once(self,**kwargs):self.calls.append(kwargs);raise TimeoutError()
  c=TimeoutClient(); t=DailyMaKISOrderTransport(client=c,config=DailyMaKISOrderTransportConfig('12345678','01',frozenset({'005930'})))
  runtime=DailyMaBrokerSubmitRuntime(store=InMemoryDailyMaSubmitStore(),transport=t,profile=DailyMaSendProfile(enabled=True)); _,first=runtime.submit(order()); _,second=runtime.submit(order()); self.assertEqual(first,'UNKNOWN_BROKER_STATE'); self.assertEqual(second,'RESEND_FORBIDDEN'); self.assertEqual(len(c.calls),1)
