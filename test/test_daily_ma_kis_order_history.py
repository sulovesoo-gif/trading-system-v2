import unittest
from datetime import date, datetime

from src.broker.contracts import BrokerOrder, BrokerOrderStatus
from src.daily_ma_v03.actual_submit import DailyMaBrokerSubmitRuntime, InMemoryDailyMaSubmitStore
from src.daily_ma_v03.kis_order_history import DailyMaKISOrderHistoryLookup, UnknownResolution
from src.daily_ma_v03.send_authorization import DailyMaSendProfile


class Client:
 def __init__(self,rows):self.rows=rows;self.calls=[]
 def get(self,**kwargs):self.calls.append(kwargs);return {'rt_cd':'0','output1':self.rows}
class Account: cano='12345678';account_product_code='01'
class TimeoutTransport:
 def submit_once(self,*_,**__):raise TimeoutError()
def order():return BrokerOrder('broker','request','daily-ma','005930','BUY',7,'key',BrokerOrderStatus.SUBMITTING,{'order_policy':'DAILY_MA_KRX_MARKET'},created_at=datetime(2026,8,25,15,19))
def row(**changes):
 base={'ord_dt':'20260825','odno':'000123','ord_gno_brno':'06010','pdno':'005930','sll_buy_dvsn_cd':'02','ord_qty':'7','avg_prvs':'70000','tot_ccld_qty':'7','rmn_qty':'0','rjct_qty':'0','cncl_yn':'','ord_tmd':'151901'};base.update(changes);return base

class DailyMaKisOrderHistoryTest(unittest.TestCase):
 def test_current_official_tr_and_exact_query_contract(self):
  client=Client([row()]);lookup=DailyMaKISOrderHistoryLookup(client=client,account=Account()); found=lookup.orders_for_day(order_date=date(2026,8,25),stock_code='005930',side='BUY');self.assertEqual(lookup.tr_id,'TTTC0081R');self.assertEqual(found[0].order_number,'000123');self.assertEqual(client.calls[0]['params']['EXCG_ID_DVSN_CD'],'KRX');self.assertEqual(client.calls[0]['params']['INQR_DVSN_3'],'01')
 def test_unknown_recovery_uses_lookup_once_and_never_resends(self):
  runtime=DailyMaBrokerSubmitRuntime(store=InMemoryDailyMaSubmitStore(),transport=TimeoutTransport(),profile=DailyMaSendProfile(enabled=True)); current=order();self.assertEqual(runtime.submit(current)[1],'UNKNOWN_BROKER_STATE');lookup=DailyMaKISOrderHistoryLookup(client=Client([row()]),account=Account());self.assertEqual(runtime.recover_unknown(order=current,history_lookup=lookup,order_date=date(2026,8,25))[1],'RECOVERED_ACK');self.assertEqual(runtime.submit(current)[1],'RESEND_FORBIDDEN')
 def test_missing_or_ambiguous_history_is_fail_closed(self):
  self.assertEqual(DailyMaKISOrderHistoryLookup.resolve(records=(),expected_quantity=7)[0],UnknownResolution.UNRESOLVED);self.assertEqual(DailyMaKISOrderHistoryLookup.resolve(records=(DailyMaKISOrderHistoryLookup._parse(row()),DailyMaKISOrderHistoryLookup._parse(row(odno='000124'))),expected_quantity=7)[0],UnknownResolution.UNRESOLVED)
