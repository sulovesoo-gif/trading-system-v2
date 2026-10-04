from datetime import datetime,timedelta,time
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4
import pytest
from src.first_rise_breakout.v2_capacity import *
from src.first_rise_breakout.models import CandidateState,ResearchState,MinuteBar

DAY=datetime(2026,10,5,9)
CONFIG=SimpleNamespace(start_slot_amount=10000000,slot_step_amount=10000000,max_slot_amount=100000000)
CAP=CapacityConfig.from_row(('Y','10','10000000','30','100','3','5','10'))

@pytest.mark.parametrize('previous,amount',[('broken','100'),('100','broken'),('broken','broken')])
def test_same_response_invalid_fields_preserve_ohlc(previous,amount):
    from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
    from src.first_rise_breakout.minute_source import SameDayMinutePeakSource
    payload={'output1':{'stck_prdy_clpr':previous},'output2':[dict(
        stck_bsop_date='20261005',stck_cntg_hour='090000',stck_oprc='100',
        stck_hgpr='101',stck_lwpr='98',stck_prpr='100',cntg_vol='10',acml_tr_pbmn=amount)]}
    calls=[]
    client=SimpleNamespace(last_payload=payload,get=lambda **kw:(calls.append(kw) or payload))
    source=SameDayMinutePeakSource(StockMinuteCollector(client));source.audit_previous_close=False
    rows=source._collect(stock_code='123456',market_code='KOSPI',input_hour='090100')
    assert len(calls)==1 and rows[0]['high_price']==101
    assert payload['output1']['stck_prdy_clpr']==previous
    assert payload['output2'][0]['acml_tr_pbmn']==amount
    if amount=='broken':
        assert rows[0]['accumulated_amount'] is None
        assert rows[0]['raw_payload']['invalid_acml_tr_pbmn']=='broken'

def bar(at,high=100,low=100,opening=100,amount=1):
    return MinuteBar(at,D(opening),D(high),D(low),D(opening),1,D(amount) if amount is not None else None)

@pytest.mark.parametrize('seconds,allowed',[(359,False),(360,True),(361,True)])
@pytest.mark.parametrize('previous',[None,D(0),D(99),D(100000)])
def test_v2_rest_no_previous_gate(seconds,allowed,previous):
    f=V2Strategy();f.ENTRY_START=time(9)
    s=CandidateState(uuid4(),DAY.date(),'123456',ResearchState.DISCOVERED)
    s=f.observe_bar(s,bar(DAY),previous_close=previous).after
    s=f.observe_bar(s,bar(DAY+timedelta(minutes=1),low='98.4'),previous_close=previous).after
    d=f.observe_bar(s,bar(DAY+timedelta(seconds=seconds),high=101),previous_close=previous)
    assert d.create_entry==allowed

@pytest.mark.parametrize('low,allowed',[('98.401',False),('98.400',True),('90.000',True),('89.999',False)])
def test_v2_pullback(low,allowed):
    f=V2Strategy();f.ENTRY_START=time(9)
    s=CandidateState(uuid4(),DAY.date(),'123456',ResearchState.DISCOVERED)
    s=f.observe_bar(s,bar(DAY)).after
    s=f.observe_bar(s,bar(DAY+timedelta(minutes=1),low=low)).after
    d=f.observe_bar(s,bar(DAY+timedelta(minutes=6),high=102,opening=101))
    assert d.create_entry==allowed
    if allowed:assert d.raw_execution_price==101

@pytest.mark.parametrize('cash,amount,target',[(500000000,800000000,80000000),(500000000,5000000000,150000000),(50000000,5000000000,50000000)])
def test_unbounded_liquidity_sizing(cash,amount,target):
    qty,used,e=capacity_sizing(config=CONFIG,capacity=CAP,realized_net_pnl=140000000,
        broker_cash=cash,reservation=0,price=100,buy_fee_rate=0,
        liquidity={'reason':'OK','recent_5m_traded_amount':str(amount)})
    assert D(e['target_cash'])==target
    assert D(e['common_slot_amount'])==150000000
    assert used==target and qty==target//100

def test_completed_amount_differences():
    bars=[bar(DAY+timedelta(minutes=i),amount=(i+1)*100) for i in range(7)]
    assert recent_liquidity(bars,signal_time=DAY+timedelta(minutes=4))['recent_5m_traded_amount']=='500'
    assert recent_liquidity(bars,signal_time=DAY+timedelta(minutes=5))['minute_amounts']==['100']*5
    assert recent_liquidity(bars[:4],signal_time=DAY+timedelta(minutes=3))['reason']!='OK'
    bars[3]=bar(DAY+timedelta(minutes=3),amount=1)
    assert recent_liquidity(bars,signal_time=DAY+timedelta(minutes=5))['reason']!='OK'

@pytest.mark.parametrize('gap,level',[('2.99','NORMAL'),('3','WARNING'),('4.99','WARNING'),('5','STRONG_WARNING'),('9.99','STRONG_WARNING'),('10','CRITICAL_REVIEW')])
def test_warning_thresholds(gap,level):
    rows=[(D('.01'),D('.01')*(1-D(gap)/100))]*100
    assert rolling_comparison(rows,count=30,config=CAP)['level']==level
    assert rolling_comparison(rows,count=100,config=CAP)['level']==level
    assert rolling_comparison(rows[:29],count=30,config=CAP)['status']=='INSUFFICIENT_MATCHED_TRADES'
    assert rolling_comparison(rows[:99],count=100,config=CAP)['status']=='INSUFFICIENT_MATCHED_TRADES'

def test_shadow_fixed_and_nonpositive_denominator():
    for exit_price in (90,110):
        r=shadow_result(raw_entry=100,raw_exit=exit_price)
        assert r['shadow_fixed_amount']==10000000
        assert r['shadow_quantity']==shadow_result(raw_entry=100)['shadow_quantity']
    for s in (0,-1):
        r=rolling_comparison([(s,-2)]*30,count=30,config=CAP)
        assert r['level']=='NORMAL' and r['degradation_pct'] is None


@pytest.mark.parametrize('amount',[None,'NaN','Infinity','abc','-1','0'])
def test_invalid_liquidity_does_not_produce_quantity(amount):
    bars=[bar(DAY+timedelta(minutes=i),amount=100*(i+1)) for i in range(5)]
    from dataclasses import replace
    bars[-1]=replace(bars[-1],accumulated_amount=amount)
    liquid=recent_liquidity(bars,signal_time=bars[-1].bar_time)
    assert liquid['reason']!='OK'
    qty,_,_=capacity_sizing(config=CONFIG,capacity=CAP,realized_net_pnl=0,broker_cash=50000000,
        reservation=0,price=100,buy_fee_rate=0,liquidity=liquid)
    assert qty==0


def test_reservation_cash_not_warning_controls_sizing():
    liquid={'reason':'OK','recent_5m_traded_amount':'5000000000'}
    qty,used,evidence=capacity_sizing(config=CONFIG,capacity=CAP,realized_net_pnl=140000000,
        broker_cash=50000000,reservation=43000000,price=100,buy_fee_rate=0,liquidity=liquid)
    assert used==7000000 and qty==70000
    # Capacity warnings have no input into this function / order eligibility.
    assert 'warning' not in evidence and D(evidence['common_slot_amount'])>100000000
