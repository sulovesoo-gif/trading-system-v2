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
    with unittest.TestCase().assertRaises(PermissionError): NoSendBoundary.submit()
    assert NoSendBoundary.actual_post_count==0 and SEND_ENABLED is False


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(unittest.FunctionTestCase(fn) for name,fn in globals().items()
                              if name.startswith('test_') and callable(fn))
