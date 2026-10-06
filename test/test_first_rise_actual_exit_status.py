import json
import threading
from contextlib import contextmanager
from datetime import datetime,timedelta
from decimal import Decimal as D
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import Mock,patch

import pytest

from src.first_rise_breakout.v2_capacity_repository import CapacityMonitor
from src.service.first_rise_status_service import lifecycle,operation_logs,snapshot
from scripts.dashboard.serve_first_rise_status import handler


DAY=datetime(2026,10,6)


@pytest.mark.parametrize('protection',[True,False])
def test_actual_exit_capacity_uses_owned_observation_not_later_market_exit(protection):
    actual=DAY.replace(hour=14,minute=10,second=4)
    market=DAY.replace(hour=14,minute=14 if protection else 9)
    reason='ACTUAL_STOP_ENTRY_BREAK_PROTECTION' if protection else 'STOP_ENTRY_BREAK'
    market_liquidity={'recent_5m_traded_amount':'99999' if protection else '500'}
    row=('trade','signal','123456',1,DAY.replace(hour=9,minute=30),market,'STOP_ENTRY_BREAK',
         D('.1'),D(1000),D(1100),10,10,D(1),None,D(90),None,actual,
         {'recent_5m_traded_amount':'10000'},{'exit_liquidity':market_liquidity},D(100),D(110))
    q=Mock();q.fetchall.side_effect=[[row],[],[(DAY.replace(hour=14,minute=4)+timedelta(minutes=i),D((i+1)*100)) for i in range(6)]]
    q.fetchone.return_value=(actual,reason)
    @contextmanager
    def connection():yield SimpleNamespace(cursor=lambda:cursor(),transaction=lambda:transaction())
    @contextmanager
    def cursor():yield q
    @contextmanager
    def transaction():yield
    monitor=CapacityMonitor(SimpleNamespace(connection=connection));monitor.deliver=Mock()
    with patch('src.first_rise_breakout.v2_capacity_repository.capacity_day',return_value=None):
        monitor.refresh(at=actual+timedelta(minutes=10))
    call=next(c for c in q.execute.call_args_list if 'INSERT INTO first_rise_j_capacity_observation' in c.args[0])
    params=call.args[1];evidence=params[-1].obj
    assert params[5:7]==(market,'STOP_ENTRY_BREAK')
    assert evidence['market_exit_time']==market.isoformat()
    assert evidence['market_exit_liquidity']==market_liquidity
    assert evidence['actual_exit_observed_at']==actual.isoformat()
    assert evidence['actual_exit_reason']==reason
    assert evidence['actual_exit_recent_5m_amount']=='500'
    assert D(evidence['actual_exit_participation_pct'])==220
    assert evidence['actual_sell_average_price']=='110'
    raw_call=next(c for c in q.execute.call_args_list if 'FROM first_rise_completed_minute_raw' in c.args[0])
    assert raw_call.args[1][-2:]==(actual.replace(second=0),actual)
    assert not any('UPDATE first_rise_j_market_signal' in c.args[0] for c in q.execute.call_args_list)


def sample():
    return dict(signal=dict(market_signal_id='s',entry_signal_time='2026-10-06T13:57:00',
        stock_code='049080',signal_sequence=2,raw_entry_price=11820,
        entry_evidence={'sequence_replay_only':True,'live_entry_eligible':False},
        exit_reason='BOOK_TENKAN_PROFIT',exit_signal_time='2026-10-06T14:13:00',
        exit_execution_time='2026-10-06T14:14:00'),
        stock_name='fixture',discovered_at='2026-10-06T14:00:00')


def test_readonly_status_endpoint_render_and_copy_smoke():
    queries=[];results=iter([[(sample(),)],[(1,1,0,'2026-10-06T13:57:00')],[(1,)],[(0,)],[(0,)],[(D(10000000),)],[(0,0,0,0,0,0,0,0)]])
    class Cursor:
        def execute(self,sql,args=None):
            assert sql.lstrip().split()[0] in ('SET','SELECT')
            queries.append(sql)
            if sql.lstrip().startswith('SELECT'):self.rows=next(results)
        def fetchall(self):return self.rows
        def fetchone(self):return self.rows[0]
    q=Cursor()
    @contextmanager
    def transaction():yield
    @contextmanager
    def cursor():yield q
    @contextmanager
    def connection():yield SimpleNamespace(transaction=transaction,cursor=cursor)
    unavailable=lambda:operation_logs(run=lambda *a,**k:SimpleNamespace(returncode=1,stdout='',stderr='Permission denied'))
    data=snapshot(SimpleNamespace(connection=connection),DAY.date(),logs=unavailable)
    assert queries[0]=='SET TRANSACTION READ ONLY'
    assert data['log_access']=='LOG_ACCESS_UNAVAILABLE'
    assert '과거신호 복원' in data['rows'][0]['copy_text']
    assert 'BOOK_TENKAN_PROFIT' in data['rows'][0]['copy_text']
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(lambda:data))
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}'
        with urlopen(url) as r:page=r.read().decode()
        assert '최근 오류 전체 복사' in page and 'navigator.clipboard.writeText' in page
        assert 'setTimeout(refresh,8000)' in page
        assert 'innerHTML' not in page
        with urlopen(url+'/api/status') as r:assert json.load(r)['summary']['replay']==1
        with pytest.raises(HTTPError) as failure:urlopen(Request(url+'/api/status',data=b'{}',method='POST'))
        assert failure.value.code==405
    finally:server.shutdown();server.server_close();thread.join()


def test_error_copy_text_is_safe_and_bounded():
    event=json.dumps({'MESSAGE':'ERROR access_token=SECRET failure','PRIORITY':'6','__REALTIME_TIMESTAMP':'1791255004000000'})
    def run(args,**kw):
        assert 'sudo' not in args
        return SimpleNamespace(returncode=0,stderr='',stdout=event if args[0]=='journalctl' else 'ActiveState=active\nMainPID=123')
    data=operation_logs(run)
    assert len(data['logs'])==2
    copied=data['logs'][0]['copy_text']
    assert 'KST]' in copied and 'service=' in copied and 'level=ERROR' in copied
    assert 'SECRET' not in copied


def test_status_distinguishes_market_and_protection_times_without_guessing():
    protected=sample();protected.update(cost=dict(buy_quantity=10,buy_amount=D(118700),sell_quantity=10,
        sell_amount=D(118100),provisional_applied_at='2026-10-06T14:01:00'),capacity={},intent={},paper=None,shadow=None,
        orders=[dict(side='SELL',created_at='2026-10-06T14:00:01',broker_created_at='2026-10-06T14:00:02',trigger=dict(
            actual_exit_reason='ACTUAL_STOP_ENTRY_BREAK_PROTECTION',observation_timestamp='2026-10-06T14:00:00',
            stop_reference_price='11820',observed_market_price='11810'),
            observation=dict(first_fill_observed_at='2026-10-06T14:00:03'))])
    row=lifecycle(protected)
    assert row['market_exit_signal_time']=='2026-10-06T14:13:00'
    assert row['market_exit_time']=='2026-10-06T14:14:00'
    assert row['protection_trigger_time']=='2026-10-06T14:00:00'
    assert row['protection_reference_price']=='11820'
    assert row['protection_observed_price']=='11810'
    assert row['actual_sell_order_time']=='2026-10-06T14:00:02'
    assert row['actual_exit_time']=='2026-10-06T14:00:03'
    assert '5초 보호청산=조건 2026-10-06T14:00:00' in row['copy_text']

    historical=sample();historical.update(cost={},capacity={},intent={},paper=None,shadow=None,orders=[])
    old=lifecycle(historical)
    assert old['protection_trigger_time'] is None
    assert old['actual_sell_order_time'] is None
    assert old['actual_exit_time'] is None


def test_shared_dashboard_first_rise_routes_are_readonly(monkeypatch):
    from scripts.dashboard import serve_multi_ma_dashboard as dashboard
    calls=[]
    def load(pool,day):
        calls.append(day)
        return {'summary':{'signals':1}}
    monkeypatch.setattr(dashboard,'first_rise_status_snapshot',load)
    server=ThreadingHTTPServer(('127.0.0.1',0),dashboard.DashboardHandler)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}'
        with urlopen(url+'/first-rise/') as response:
            page=response.read().decode()
            assert 'frame-ancestors' in response.headers['Content-Security-Policy']
        assert '<tbody id="rows">' in page and 'min-width:1420px' in page
        assert '/first-rise/api/status' in page and 'copyButton(r.copy_text)' in page
        for label in ('과거신호 복원','실주문 가능','실제 보유','주문 대기 / 상태 미확정',
                      '독립 시장청산','5초 보호청산 / 실제 매도'):
            assert label in page
        with urlopen(url+'/first-rise/api/status') as response:
            assert json.load(response)['summary']['signals']==1
        for method in ('POST','PUT','PATCH','DELETE'):
            with pytest.raises(HTTPError) as failure:
                urlopen(Request(url+'/first-rise/api/status',data=b'{}',method=method))
            assert failure.value.code==405
        assert len(calls)==1
    finally:server.shutdown();server.server_close();thread.join()
