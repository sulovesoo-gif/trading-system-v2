from datetime import datetime
from decimal import Decimal
import unittest
from src.flow_v3.live_contract import *
from src.flow_v3.live_broker import NoSendBoundary


def test_exact_whitelist_and_market_sell_for_both_directions():
    assert len(WHITELIST)==15 and len(LONG_IDS)==11 and len(SHORT_IDS)==4
    for sid,(direction,code) in WHITELIST.items():
        validate_mapping(sid,'000660',direction,code)
        assert request_payload(code,'SELL',2)['body']==dict(PDNO=code,ORD_DVSN='01',ORD_QTY='2',ORD_UNPR='0',EXCG_ID_DVSN_CD='KRX',SLL_TYPE='01')
    with unittest.TestCase().assertRaises(ValueError): validate_mapping('FV3000001','000660','LONG','0193T0')
    with unittest.TestCase().assertRaises(ValueError): validate_mapping(LONG_IDS[0],'000660','LONG','000660')


def test_variable_quantity_and_only_realized_balance():
    assert [order_quantity(x,100) for x in (150,199,200,300,-1)]==[1,1,2,3,0]
    with unittest.TestCase().assertRaises(ValueError): order_quantity(100,0)
    with unittest.TestCase().assertRaises(ValueError): order_quantity(100,Decimal('NaN'))


def test_checkpoint_duplicate_regression_overfill():
    assert cumulative_delta(1,100,1,100,3)==(0,0)
    assert cumulative_delta(1,100,3,310,3)==(2,210)
    for args in [(1,100,0,0,3),(1,100,1,101,3),(1,100,4,410,3)]:
        with unittest.TestCase().assertRaises(ValueError): cumulative_delta(*args)


def test_lot_exit_family_and_hold_next_day():
    e=dict(entry_signal_time=datetime(2026,9,8,10),exit_policy_code='SIGNAL_EOD',
           exit_fast_period=1,exit_slow_period=3,entry_family_code='F1',direction='LONG')
    s=dict(is_complete=True,bar_time=datetime(2026,9,8,15,18),flow_crosses={'01':-1},velocity_crosses={'01':1})
    assert normal_exit(e,s)
    assert not normal_exit(dict(e,entry_family_code='F2'),s)
    assert not normal_exit(e,dict(s,bar_time=datetime(2026,9,8,15,19)))
    assert not normal_exit(e,dict(s,bar_time=datetime(2026,9,9,10)))
    assert normal_exit(dict(e,exit_policy_code='SIGNAL_HOLD'),dict(s,bar_time=datetime(2026,9,9,10)))
    assert not normal_exit(e,dict(s,is_complete=False))


def test_physical_no_send():
    from src.flow_v3.live_transport import FlowTransport
    with unittest.TestCase().assertRaises(PermissionError): NoSendBoundary.submit()
    # Even without a usable repository/client/account, no IO is attempted.
    assert FlowTransport(None,None,None).run()==0
    assert NoSendBoundary.actual_post_count==0 and SEND_ENABLED is False


def test_transport_claim_response_with_fake_io_only():
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import patch
    from src.flow_v3.live_transport import FlowTransport
    class Connection:
        available=True
        row=None
        def transaction(self): return nullcontext()
        def execute(self,sql,args=None):
            self.row=None
            if 'SELECT o.broker_order_id' in sql and self.available:
                self.row=('fixture-order',request_payload('0193T0','BUY',2))
            if "SET status='SUBMITTING'" in sql:
                self.available=False
            return self
        def fetchone(self): return self.row
    conn=Connection();calls=[];responses=[]
    repo=SimpleNamespace(pool=SimpleNamespace(connection=lambda:nullcontext(conn)),
                         record_response=lambda *args:responses.append(args))
    client=SimpleNamespace(post_once=lambda **kw:(calls.append(kw) or {'rt_cd':'0','output':{'ODNO':'fixture'}}))
    adapter=FlowTransport(repo,client,SimpleNamespace(cano='fixture',account_product_code='00'))
    with patch('src.flow_v3.live_transport.SEND_ENABLED',True):
        assert adapter.run()==1
        assert adapter.run()==0
    assert len(calls)==len(responses)==1 and calls[0]['payload']['ORD_QTY']=='2'


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(fn) for name,fn in globals().items()
                              if name.startswith('test_') and callable(fn))
