"""Opt-in PostgreSQL/HTTP tests; history is TEMP, never the production ledger.

SQL_ANALYSIS_TEST_ENV=/path/to/.env python -m unittest test.test_sql_analysis_cancel -v
"""
import os
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import contextmanager
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from src.service.sql_analysis_runner_service import SqlAnalysisRunner, SqlAnalysisSettings, StreamingXlsxWriter

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.getenv('SQL_ANALYSIS_TEST_ENV'), 'requires opt-in isolated PostgreSQL session')
class CancelIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from dotenv import load_dotenv
        load_dotenv(os.environ['SQL_ANALYSIS_TEST_ENV'])
        cls.settings = SqlAnalysisSettings.from_environment(ROOT)
        cls.history = psycopg.connect(host=os.environ['DB_HOST'], port=os.environ['DB_PORT'],
            dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], autocommit=True)
        initial = (ROOT / 'database/migrations/20260828_sql_analysis_runner_additive.sql').read_text()
        cls.history.execute(initial.replace('CREATE TABLE IF NOT EXISTS', 'CREATE TEMP TABLE'))
        cls.history.execute((ROOT / 'database/migrations/20260910_sql_analysis_cancel.sql').read_text())
        cls.history_lock = threading.RLock()

    @classmethod
    def tearDownClass(cls):
        cls.history.close()  # drops only this session's TEMP history

    def setUp(self):
        import requests
        from scripts.dashboard.serve_multi_ma_dashboard import DashboardHandler
        self.requests = requests
        self.temp = tempfile.TemporaryDirectory()
        self.runner = SqlAnalysisRunner(self, replace(self.settings, artifact_dir=Path(self.temp.name)))
        handler = type('IsolatedAnalysisHandler', (DashboardHandler,), {'sql_runner': self.runner})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port) + '/sql-analysis/api'
        self.headers = {'X-Analysis-Key': self.settings.auth_token}

    @contextmanager
    def connection(self):
        with self.history_lock:
            yield self.history

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        if self.runner._active_execution:
            self.runner.cancel_execution(str(self.runner._active_execution))
        self.runner._executor.shutdown(wait=True)
        self.runner.close()
        self.temp.cleanup()

    def submit(self, sql):
        response = self.requests.post(self.url + '/run', headers=self.headers,
            json={'sql': sql, 'request_key': str(uuid.uuid4())}, timeout=5)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['execution_id']

    def wait(self, execution_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            item = self.runner.get_execution(execution_id)
            if item['status'] in {'SUCCEEDED', 'FAILED', 'CANCELLED'} and self.runner._active_execution is None:
                return item
            time.sleep(.02)
        self.fail('worker did not finish within 8 seconds')

    def running(self, execution_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            conn = self.runner._connection
            if conn:
                with self.connection() as history:
                    active = history.execute("SELECT state='active' FROM pg_stat_activity WHERE pid=%s",
                                             (conn.info.backend_pid,)).fetchone()
                if active and active[0]:
                    return conn.info.backend_pid
            time.sleep(.02)
        self.fail('query never reached backend')

    def test_real_cancel_temp_reuse_auth_identity_and_artifact(self):
        first = self.submit('CREATE TEMP TABLE cancel_probe AS SELECT 1 AS n; SELECT * FROM cancel_probe;')
        self.assertEqual(self.wait(first)['status'], 'SUCCEEDED')
        download = self.requests.get(self.url + '/download/' + first, headers=self.headers, timeout=5)
        from scripts.verify.verify_sql_analysis_runner_e2e import workbook_evidence
        self.assertGreater(workbook_evidence(download.content)['worksheet_count'], 0)
        target = self.submit('SELECT pg_sleep(30);')
        pid = self.running(target)
        self.assertEqual(self.requests.post(self.url+'/cancel', json={'execution_id': target}, timeout=5).status_code, 401)
        self.assertEqual(self.requests.post(self.url+'/cancel', headers=self.headers, json={}, timeout=5).status_code, 409)
        self.assertEqual(self.requests.post(self.url+'/cancel', headers=self.headers,
            json={'execution_id': str(uuid.uuid4())}, timeout=5).status_code, 404)
        self.assertFalse(self.runner.cancel_execution(first)['cancel_requested'])
        self.assertEqual(self.runner.get_execution(target)['status'], 'RUNNING')
        started = time.monotonic()
        response = self.requests.post(self.url+'/cancel', headers=self.headers, json={'execution_id': target}, timeout=5)
        self.assertTrue(response.json()['cancel_requested'])
        done = self.wait(target)
        self.assertEqual(done['status'], 'CANCELLED')
        self.assertEqual(done['error_message'], 'Cancelled by user')
        self.assertLess(time.monotonic()-started, 5)
        self.assertIsNotNone(done['finished_at'])
        self.assertFalse(self.runner.cancel_execution(target)['cancel_requested'])
        self.assertFalse((Path(self.temp.name)/f'{target}.xlsx').exists())
        self.assertEqual(list(Path(self.temp.name).glob('sql-xlsx-*')), [])
        with self.connection() as history:
            self.assertNotEqual(history.execute('SELECT state FROM pg_stat_activity WHERE pid=%s', (pid,)).fetchone()[0], 'active')
        self.assertEqual(self.runner._connection.info.backend_pid, pid)
        self.assertEqual(self.wait(self.submit('SELECT * FROM cancel_probe;'))['status'], 'SUCCEEDED')

    def test_queued_cancel_never_executes(self):
        release = threading.Event()
        self.runner._executor.submit(release.wait, 5)
        with patch.object(self.runner, '_connect', wraps=self.runner._connect) as connect:
            target = self.submit('SELECT 42;')
            self.assertEqual(self.runner.cancel_execution(target)['status'], 'QUEUED')
            release.set()
            self.assertEqual(self.wait(target)['status'], 'CANCELLED')
            connect.assert_not_called()

    def test_timeout_is_failed_and_session_end_cleans_temp(self):
        self.assertEqual(self.wait(self.submit("SET statement_timeout='100ms';"))['status'], 'SUCCEEDED')
        done = self.wait(self.submit('SELECT pg_sleep(2);'))
        self.assertEqual((done['status'], done['error_sqlstate']), ('FAILED', '57014'))
        self.assertEqual(self.wait(self.submit('SET statement_timeout=0; CREATE TEMP TABLE end_probe(n int);'))['status'], 'SUCCEEDED')
        target = self.submit('SELECT pg_sleep(30);')
        self.running(target)
        self.assertTrue(self.runner.end_session()['pending'])
        self.assertEqual(self.wait(target)['status'], 'CANCELLED')
        self.assertIsNone(self.runner._connection)
        self.assertEqual(self.wait(self.submit('SELECT * FROM end_probe;'))['error_sqlstate'], '42P01')

    def test_export_cancel_aborts_completed_zip(self):
        entered, release = threading.Event(), threading.Event()
        close = StreamingXlsxWriter.close
        def delayed(writer):
            close(writer)
            entered.set()
            release.wait(5)
        with patch.object(StreamingXlsxWriter, 'close', delayed):
            target = self.submit('SELECT 1;')
            self.assertTrue(entered.wait(5))
            self.runner.cancel_execution(target)
            release.set()
            self.assertEqual(self.wait(target)['status'], 'CANCELLED')
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_execute_dispatch_race_retries_cancel(self):
        import psycopg
        entered, release = threading.Event(), threading.Event()
        original = psycopg.Cursor.execute
        def delayed(cur, sql, *args, **kwargs):
            if sql == 'SELECT pg_sleep(30) /* dispatch race */;':
                entered.set()
                release.wait(5)
            return original(cur, sql, *args, **kwargs)
        with patch.object(psycopg.Cursor, 'execute', delayed):
            target = self.submit('SELECT pg_sleep(30) /* dispatch race */;')
            self.assertTrue(entered.wait(5))
            self.runner.cancel_execution(target)
            time.sleep(.2)  # first cancel reaches an idle backend before execute
            release.set()
            self.assertEqual(self.wait(target)['status'], 'CANCELLED')

    def test_restricted_role_privileges_remain_readonly_temp(self):
        conn = self.runner._connect()
        self.assertTrue(conn.execute("SELECT has_database_privilege(current_database(),'TEMP')").fetchone()[0])
        self.assertFalse(conn.execute("SELECT has_schema_privilege('public','CREATE')").fetchone()[0])
        for privilege in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE'):
            self.assertFalse(conn.execute('SELECT has_table_privilege(%s,%s)',
                ('public.sql_analysis_execution_history', privilege)).fetchone()[0])

    def test_existing_e2e_safe_regression(self):
        import sys
        from contextlib import redirect_stdout
        from io import StringIO
        from scripts.verify import verify_sql_analysis_runner_e2e as e2e
        fixture = Path(self.temp.name)/'fixture.sql'
        fixture.write_text('CREATE TEMP TABLE tmp_mm_param AS SELECT 1 AS n; SELECT * FROM tmp_mm_param;')
        self.close = lambda: None  # pool interface; fixture connection remains owned by test class
        with patch.object(e2e, 'create_connection_pool', return_value=self), \
             patch.object(sys, 'argv', ['verify', str(fixture), '--base-url', self.url.removesuffix('/sql-analysis/api'), '--safe-only']), \
             redirect_stdout(StringIO()):
            self.assertEqual(e2e.main(), 0)


class CopyContractTest(unittest.TestCase):
    def test_copy_fallback_and_cancel_endpoint(self):
        page = (ROOT/'reports/multi-ma/sql-analysis.html').read_text(encoding='utf-8')
        for contract in ('window.isSecureContext', "document.execCommand('copy')", 'setSelectionRange',
                         'area.remove()', '직접 복사', '/sql-analysis/api/cancel', 'cancelInFlight'):
            self.assertIn(contract, page)


if __name__ == '__main__':
    unittest.main()
