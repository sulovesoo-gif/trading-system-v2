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
from src.daily_ma_v03.actual_submit import DailyMaDurableSubmitService,SubmitRecord,SubmitState

class Client:
    def __init__(self):self.calls=0
    def post_once(self,**kwargs):self.calls+=1;return {'rt_cd':'0','output':{'ODNO':'1'}}

class MinuteActualSendTest(unittest.TestCase):
    def test_explicit_rejection_is_durable_and_never_resent(self):
        class Store:
            def __init__(self):self.claims=0;self.rejections=[]
            def claim(self,request_key):
                self.claims+=1
                return None if self.claims>1 else SimpleNamespace(client_order_key=request_key)
            def reject(self,**kwargs):self.rejections.append(kwargs['raw'])
        class Runtime:
            def submit(self,order):
                return SubmitRecord(order.client_order_key,SubmitState.REJECTED,
                  broker_response_code='APBK0919',broker_response_message='explicit reject',
                  broker_response={'rt_cd':'1','msg_cd':'APBK0919','msg1':'explicit reject'}),'REJECTED'
        store=Store();service=DailyMaDurableSubmitService(store=store,runtime=Runtime())
        self.assertEqual('REJECTED',service.submit_request('reject-key')[1])
        self.assertEqual({'rt_cd':'1','msg_cd':'APBK0919','msg1':'explicit reject'},store.rejections[0])
        self.assertEqual('RESEND_FORBIDDEN',service.submit_request('reject-key')[1])

    def test_transport_requires_minute_policy_and_profile(self):
        class Recorder:
            def __init__(self):self.count=0
            def mark_post_attempted(self,**_):self.count+=1
        client=Client();recorder=Recorder();transport=MinuteMaKISOrderTransport(client=client,
          config=MinuteMaKISOrderTransportConfig('1','1',frozenset({'0193T0'})),attempt_recorder=recorder)
        order=BrokerOrder('b','r','s','0193T0','BUY',2,'k',BrokerOrderStatus.SUBMITTING,{'order_policy':'MINUTE_MA_KRX_MARKET'})
        with self.assertRaises(PermissionError):transport.submit_once(order,profile=MinuteMaSendProfile(enabled=False))
        self.assertEqual(transport.submit_once(order,profile=MinuteMaSendProfile(enabled=True))['rt_cd'],'0')
        self.assertEqual(client.calls,1);self.assertEqual(recorder.count,1)

    def test_environment_is_fail_closed(self):
        with patch.dict(os.environ,{},clear=True):self.assertFalse(MinuteMaSendProfile.from_environment().enabled)

    def test_rejection_migration_and_dashboard_lifecycle_contract(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        sql=(root/'database/migrations/20260831_minute_ma_reject_recovery_additive.sql').read_text(encoding='utf-8')
        self.assertIn('minute_ma_live_broker_submit_attempt',sql)
        self.assertIn('minute_ma_live_broker_rejection',sql)
        self.assertIn('response_code',sql);self.assertIn('response_message',sql)
        page=(root/'reports/multi-ma/minute-ma.html').read_text(encoding='utf-8')
        for label in ('today_post_attempts','today_acknowledged','today_rejected','today_unknown'):
            self.assertIn(label,page)
        self.assertIn('recent_rejections',page)
        self.assertIn('응답코드',page);self.assertIn('응답메시지',page)

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
