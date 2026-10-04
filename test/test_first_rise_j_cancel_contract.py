from datetime import datetime,date
from decimal import Decimal as D
from types import SimpleNamespace
import pytest
from src.first_rise_breakout.j_cancel import KRXExecutionSession
from src.broker.shared_cost_allocation import OwnedCheckpoint,allocate_shared_costs
from src.daily_ma_v03.broker_cost_allocation import BrokerCostSnapshot,BrokerCostTotals,BrokerCostStatus


def test_sell_cancel_filter_pagination_and_flow_buy_unchanged():
    from src.first_rise_breakout.j_cancel import JCancelRuntime
    from src.flow_v3.live_broker import FlowBrokerReader
    calls=[]
    def get(**kw):
        calls.append(kw)
        client.last_response_headers={'tr_cont':'M'} if len(calls)==1 else {}
        return {'output':[{'odno':'wanted','pdno':'123456'}] if len(calls)>1 else [],
                'ctx_area_fk100':'NEXT','ctx_area_nk100':'KEY'}
    client=SimpleNamespace(get=get,last_response_headers={})
    account=SimpleNamespace(cano='TEST',account_product_code='TEST')
    cancel=JCancelRuntime(None,client,account,session_open=lambda _:True)
    assert len(cancel.reader.cancellable_order('wanted','123456'))==1
    assert [c['params']['INQR_DVSN_2'] for c in calls]==['1','1']
    assert calls[1]['params']['CTX_AREA_FK100']=='NEXT'
    assert calls[1]['extra_headers']=={'tr_cont':'N'}
    FlowBrokerReader(client,account).cancellable_order('wanted','123456')
    assert calls[-1]['params']['INQR_DVSN_2']=='2'


def test_exchange_session_is_not_entry_cutoff_and_caches_day():
    calls=[]
    def days(start,end):calls.append(start);return [start] if start.weekday()<5 else []
    session=KRXExecutionSession(SimpleNamespace(open_dates=days))
    assert session(datetime(2026,10,2,15,29))
    assert not session(datetime(2026,10,2,15,30))
    assert not session(datetime(2026,10,2,8,59))
    assert len(calls)==1
    assert not session(datetime(2026,10,3,9,0))
    assert len(calls)==2


def test_shared_cost_accepts_owned_slices_not_cross_family_or_duplicate_slice():
    snapshot=BrokerCostSnapshot(date(2026,10,2),'123456',BrokerCostTotals(D(0),D(11),D(22)),
        datetime(2026,10,5,9),True,BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK)
    slices=[OwnedCheckpoint('FIRST_RISE',1,'aggregate',1,'SELL',40,D(40000)),
            OwnedCheckpoint('FIRST_RISE',2,'aggregate',1,'SELL',20,D(20000))]
    _,allocations=allocate_shared_costs(snapshot=snapshot,checkpoints=slices)
    assert sum(a.sell_fee for _,a in allocations)==11
    assert sum(a.sell_tax for _,a in allocations)==22
    with pytest.raises(ValueError):allocate_shared_costs(snapshot=snapshot,checkpoints=slices+slices[:1])
    with pytest.raises(ValueError):allocate_shared_costs(snapshot=snapshot,checkpoints=slices+[
        OwnedCheckpoint('MINUTE',3,'aggregate',1,'SELL',1,D(1000))])
