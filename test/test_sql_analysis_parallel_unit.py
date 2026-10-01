"""Real worker/XLSX paths with isolated deterministic connection doubles."""
from contextlib import contextmanager
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import uuid
import zipfile

import pytest
from src.service.sql_analysis_runner_service import SqlAnalysisSessions, SqlAnalysisRunner, SqlAnalysisSettings

class History:
    def __init__(self): self.rows={}; self.lock=threading.RLock()
    @contextmanager
    def connection(self):
        with self.lock: yield self
    @contextmanager
    def cursor(self): yield self
    def execute(self, sql, args):
        row=self.rows[str(args[-1])]
        if "SET status='RUNNING'" in sql: row['status']='RUNNING'
        elif "SET status='SUCCEEDED'" in sql:
            row.update(status='SUCCEEDED',excel_filename=args[4],total_result_rows=args[2])
        elif 'SET status=%s' in sql: row.update(status=args[0],error_message=args[3])
        elif 'SELECT source_type,original_filename' in sql:
            self.result=(row['source_type'],row['original_filename'])
        else: raise AssertionError(sql)
    def fetchone(self): return self.result

class Connection:
    def __init__(self, release):
        self.release=release;self.closed=False
        self.info=SimpleNamespace(transaction_status=0)
    def close(self): self.closed=True
    @contextmanager
    def cursor(self): yield self
    def execute(self, sql, prepare=False):
        self.value=sql;self.sent=False
        self.description=[SimpleNamespace(name='value')]
        if sql.startswith('slow'):
            assert self.release.wait(5)
        if 'error' in sql: raise ValueError('isolated failure')
        if 'timeout' in sql: raise TimeoutError('isolated timeout')
    def fetchmany(self,n):
        if self.sent:return []
        self.sent=True;return [(self.value,)]
    def nextset(self): return False

class Runner(SqlAnalysisRunner):
    def __init__(self,*args):
        super().__init__(*args);self.release=threading.Event()
    def _execution_for_request(self,key):return None
    def _record(self,sql,title,source_type,filename,request_key):
        key=uuid.uuid4()
        self.history_pool.rows[str(key)]={'execution_id':str(key),'analysis_session_id':str(self._session_id),
            'status':'QUEUED','source_type':source_type,'original_filename':filename,'excel_filename':None}
        return key
    def get_execution(self,key):return dict(self.history_pool.rows[str(key)])
    def _connect(self):
        if self._connection is None:self._connection=Connection(self.release)
        return self._connection

def wait(runner,key):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if runner.status()['active_execution_id'] is None:return runner.get_execution(key)
        time.sleep(.01)
    raise AssertionError('worker did not finish')

@pytest.mark.parametrize('suffix,status',[('ok','SUCCEEDED'),('error','FAILED'),('timeout','FAILED')])
def test_actual_worker_parallel_errors_and_workbook_isolation(tmp_path,suffix,status):
    settings=SqlAnalysisSettings('unused',5432,'unused','unused','unused','test',tmp_path)
    manager=SqlAnalysisSessions(History(),settings,runner_factory=Runner)
    a=manager.for_client(str(uuid.uuid4()));b=manager.for_client(str(uuid.uuid4()))
    try:
        aid=a.submit('slow '+suffix,'A','UPLOAD','A.sql','a')['execution_id']
        bid=b.submit('ONLY_B','B','UPLOAD','한글 B.SQL','b')['execution_id']
        assert wait(b,bid)['status']=='SUCCEEDED'
        assert a.status()['active_execution_id']==aid
        assert a._connection is not b._connection
        a.release.set()
        assert wait(a,aid)['status']==status
        bp,bname=b.artifact(bid)
        assert bname=='한글 B.xlsx'
        with zipfile.ZipFile(bp) as z:
            assert 'ONLY_B' in z.read('xl/worksheets/sheet1.xml').decode()
            assert 'slow' not in z.read('xl/worksheets/sheet1.xml').decode()
        if status=='SUCCEEDED':
            ap,aname=a.artifact(aid)
            assert ap!=bp and aname=='A.xlsx'
        assert b.status()['session_connected']
    finally:
        a.release.set()
        a._executor.shutdown(wait=True);b._executor.shutdown(wait=True)
        manager.close()

def test_bounded_sessions_reused_and_end_does_not_touch_other_tab(tmp_path):
    manager=SqlAnalysisSessions(History(),SqlAnalysisSettings('x',1,'x','x','x','x',tmp_path),
                                max_sessions=2,runner_factory=Runner)
    keys=[str(uuid.uuid4()) for _ in range(3)]
    try:
        a=manager.for_client(keys[0]);b=manager.for_client(keys[1])
        assert manager.for_client(keys[0]) is a
        with pytest.raises(RuntimeError):manager.for_client(keys[2])
        before=b.status()['session_id']
        assert manager.end_client(keys[0])['ended']
        assert manager.for_client(keys[2]) is not a
        assert b.status()['session_id']==before
    finally:manager.close()
