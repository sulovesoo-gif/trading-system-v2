import os,unittest
from datetime import datetime,date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from src.broker.contracts import BrokerOrder,BrokerOrderStatus
from src.minute_ma.kis_order_transport import MinuteMaKISOrderTransport,MinuteMaKISOrderTransportConfig
from src.minute_ma.send_authorization import MinuteMaSendProfile
from src.minute_ma.live_signal_runtime import MinuteMaLiveSignalRuntime
from src.minute_ma.contracts import Axis,MinuteMaPath
from src.minute_ma.reference_price import MinuteMaKISReferencePriceLookup

class Client:
    def __init__(self):self.calls=0
    def post_once(self,**kwargs):self.calls+=1;return {'rt_cd':'0','output':{'ODNO':'1'}}

class MinuteActualSendTest(unittest.TestCase):
    def test_transport_requires_minute_policy_and_profile(self):
        client=Client();transport=MinuteMaKISOrderTransport(client=client,config=MinuteMaKISOrderTransportConfig('1','1',frozenset({'0193T0'})))
        order=BrokerOrder('b','r','s','0193T0','BUY',2,'k',BrokerOrderStatus.SUBMITTING,{'order_policy':'MINUTE_MA_KRX_MARKET'})
        with self.assertRaises(PermissionError):transport.submit_once(order,profile=MinuteMaSendProfile(enabled=False))
        self.assertEqual(transport.submit_once(order,profile=MinuteMaSendProfile(enabled=True))['rt_cd'],'0')
        self.assertEqual(client.calls,1)

    def test_environment_is_fail_closed(self):
        with patch.dict(os.environ,{},clear=True):self.assertFalse(MinuteMaSendProfile.from_environment().enabled)

    def test_stop_anchor_uses_exact_entry_minute_open(self):
        class QuoteClient:
            def get(self,**kwargs):
                return {'output2':[{'stck_bsop_date':'20260828','stck_cntg_hour':'150000',
                  'stck_oprc':'170000','stck_hgpr':'1','stck_lwpr':'1','stck_prpr':'1',
                  'cntg_vol':'1','acml_tr_pbmn':'1'}]}
        lookup=MinuteMaKISReferencePriceLookup(QuoteClient())
        self.assertEqual(lookup.minute_open('000660',datetime(2026,8,28,15,0,1)),Decimal('170000'))
        with self.assertRaisesRegex(ValueError,'UNDERLYING_ENTRY_OPEN_REQUIRED'):
            lookup.minute_open('000660',datetime(2026,8,28,15,1,1))

    def test_first_live_start_bootstraps_without_replay(self):
        path=MinuteMaPath(1,'p',Axis.KRX_CONTINUOUS,'000660','0193T0','LONG',3,5,3,5,None,'DS')
        point=SimpleNamespace(bar_time=datetime(2026,8,27,10,0))
        class Repo:
            advanced=[]
            def live_paths(self,axis):return (path,)
            def source_bars(self,**kwargs):return ()
            def live_runtime_cursor(self,**kwargs):return None
            def advance_live_cursor(self,**kwargs):self.advanced.append(kwargs['last_source_bar_time'])
        repo=Repo();runtime=MinuteMaLiveSignalRuntime(repository=repo,planner=None,price_lookup=None,cash_lookup=None)
        runtime.engine=SimpleNamespace(prepare=lambda **kwargs:(point,))
        result=runtime.run_axis(trading_date=date(2026,8,27),axis=Axis.KRX_CONTINUOUS)
        self.assertEqual(result,{'BOOTSTRAPPED_NO_REPLAY':1});self.assertEqual(repo.advanced,[point.bar_time])

if __name__=='__main__':unittest.main()
