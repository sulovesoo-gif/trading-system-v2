"""Opt-in concurrent PostgreSQL execution; all history is connection-local TEMP."""
import os
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from src.service.sql_analysis_runner_service import SqlAnalysisSessions, SqlAnalysisSettings

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def sessions(tmp_path):
    if not os.getenv('SQL_ANALYSIS_TEST_ENV'):
        pytest.skip('requires opt-in TEMP PostgreSQL session')
    import psycopg
    from dotenv import load_dotenv
    from src.repository.database import DatabaseSettings
    load_dotenv(os.environ['SQL_ANALYSIS_TEST_ENV'])
    c=psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs(),autocommit=True)
    c.execute('SET search_path TO pg_temp')
    c.execute((ROOT/'database/migrations/20260828_sql_analysis_runner_additive.sql').read_text().replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE'))
    c.execute((ROOT/'database/migrations/20260910_sql_analysis_cancel.sql').read_text())
    lock=threading.RLock()
    class History:
        @contextmanager
        def connection(self):
            with lock: yield c
    manager=SqlAnalysisSessions(History(),replace(SqlAnalysisSettings.from_environment(ROOT),artifact_dir=tmp_path))
    yield manager
    for runner in manager._sessions.values(): runner._executor.shutdown(wait=True)
    manager.close();c.close()

def submit(runner,sql,name):
    return runner.submit(sql,name,'UPLOAD',name+'.sql',str(uuid.uuid4()))['execution_id']

def wait(runner,key):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        item=runner.get_execution(key)
        if item['status'] in ('SUCCEEDED','FAILED','CANCELLED') and runner.status()['active_execution_id'] is None:
            return item
        time.sleep(.02)
    raise AssertionError('worker timeout')

@pytest.mark.parametrize('sql,expected',[
    ("SELECT pg_sleep(1.5); SELECT 'ONLY_A' AS result",'SUCCEEDED'),
    ("SELECT pg_sleep(.2); SELECT 1/0",'FAILED'),
    ("SET statement_timeout='150ms'; SELECT pg_sleep(1)",'FAILED'),
])
def test_parallel_slow_error_timeout_and_excel_isolation(sessions,sql,expected):
    a=sessions.for_client(str(uuid.uuid4())); b=sessions.for_client(str(uuid.uuid4()))
    aid=submit(a,sql,'A');bid=submit(b,"CREATE TEMP TABLE isolated AS SELECT 'ONLY_B'::text AS result; SELECT * FROM isolated",'한글 B')
    result=wait(b,bid)
    assert result['status']=='SUCCEEDED'
    if expected=='SUCCEEDED': assert a.status()['active_execution_id']==aid
    ar=wait(a,aid)
    assert ar['status']==expected
    assert a._connection is not b._connection
    assert a.status()['session_id']!=b.status()['session_id']
    bp,name=b.artifact(bid)
    assert name=='한글 B.xlsx'
    with zipfile.ZipFile(bp) as z:
        xml=z.read('xl/worksheets/sheet1.xml').decode()
        assert 'ONLY_B' in xml and 'ONLY_A' not in xml
    if expected=='SUCCEEDED':
        ap,name=a.artifact(aid)
        assert ap!=bp and name=='A.xlsx'
        with zipfile.ZipFile(ap) as z:
            assert any('ONLY_A' in z.read(n).decode() for n in z.namelist() if n.startswith('xl/worksheets'))
    assert wait(b,submit(b,'SELECT * FROM isolated','TEMP reuse'))['status']=='SUCCEEDED'
    assert wait(a,submit(a,'SELECT * FROM isolated','TEMP must not leak'))['status']=='FAILED'

def test_bound_and_session_end_isolated(sessions):
    keys=[str(uuid.uuid4()) for _ in range(5)]
    runners=[sessions.for_client(k) for k in keys[:4]]
    with pytest.raises(RuntimeError,match='limit'): sessions.for_client(keys[4])
    before=runners[1].status()['session_id']
    assert sessions.end_client(keys[0])['ended']
    assert sessions.for_client(keys[4]) is not runners[0]
    assert runners[1].status()['session_id']==before

def test_http_tabs_execute_and_cancel_independently(sessions):
    import requests
    from http.server import ThreadingHTTPServer
    from scripts.dashboard.serve_multi_ma_dashboard import DashboardHandler
    handler=type('ParallelTempHandler',(DashboardHandler,),{'sql_runner':sessions})
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    url=f'http://127.0.0.1:{server.server_port}/sql-analysis/api'
    keys=[str(uuid.uuid4()),str(uuid.uuid4())]
    headers=[{'X-Analysis-Key':sessions.auth_token,'X-Analysis-Session':k} for k in keys]
    def post(index,sql):
        r=requests.post(url+'/run',headers=headers[index],json={'sql':sql,'request_key':str(uuid.uuid4())},timeout=5)
        assert r.status_code==202,r.text
        return r.json()['execution_id']
    try:
        aid=post(0,'SELECT pg_sleep(120) /* parallel cancellation */')
        a=sessions.for_client(keys[0]); b=sessions.for_client(keys[1])
        deadline=time.monotonic()+5
        while a._connection is None and time.monotonic()<deadline:time.sleep(.01)
        bid=post(1,'SELECT 42 AS independent')
        assert wait(b,bid)['status']=='SUCCEEDED'
        assert requests.get(url+'/status',headers=headers[0],timeout=5).json()['active_execution_id']==aid
        assert requests.get(url+'/status',headers=headers[1],timeout=5).json()['active_execution_id'] is None
        assert not requests.post(url+'/cancel',headers=headers[1],json={'execution_id':aid},timeout=5).json()['cancel_requested']
        assert requests.post(url+'/cancel',headers=headers[0],json={'execution_id':aid},timeout=5).json()['cancel_requested']
        assert wait(a,aid)['status']=='CANCELLED'
        assert b.get_execution(bid)['status']=='SUCCEEDED'
    finally:
        server.shutdown();server.server_close()
