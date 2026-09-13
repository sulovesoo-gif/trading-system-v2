"""Only fake HTTP clients. Never imports or constructs the real broker client."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from src.flow_v3.send_authorization import send_authorized
from src.flow_v3.live_transport import FlowTransport, validate_claim
from src.flow_v3.live_contract import request_payload, cancel_payload
from test.flow_v3_legacy_fixture import LONG_IDS


def fixture(cancel=False):
    payload = cancel_payload('123','branch',2) if cancel else request_payload('000660','BUY',5)
    return ('fixture',payload,LONG_IDS[0],'000660','LONG','000660','BUY',5,'123',2,'UNDERLYING',1,100)


class Connection:
    def __init__(self, row=None, cancel=False, revoke=False, db='Y'):
        self.candidate=row or fixture(cancel)
        self.cancel=cancel; self.revoke=revoke; self.db=db
        self.claimed=False; self.row=None; self.statements=[]
    def transaction(self): return nullcontext()
    def execute(self, sql, args=None):
        self.statements.append((sql,args)); self.row=None
        if 'SELECT enabled' in sql:
            self.row=('N' if self.revoke and self.claimed else self.db,)
        elif 'SELECT live_approved' in sql:
            self.row=(True,True,None,None,self.candidate[5])
        elif ('SELECT r.entry_intent_id' if self.cancel else 'SELECT o.broker_order_id') in sql and not self.claimed:
            self.row=self.candidate
        if 'UPDATE flow_v3_live_' in sql and "status='UNKNOWN'" in sql or "SET status='SUBMITTING'" in sql:
            self.claimed=True
        return self
    def fetchone(self): return self.row


class SendTests(TestCase):
    def test_four_combinations_and_malformed_default_off(self):
        for env in ('N','Y','', 'true'):
            for db in ('N','Y',None,'true'):
                with self.subTest(env=env,db=db), patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':env}):
                    self.assertEqual(send_authorized(Connection(db=db)), env=='Y' and db=='Y')

    def run_fake(self, conn, raises=False, cash_reason=None):
        calls=[]; responses=[]
        def post(**kw):
            self.assertTrue(conn.claimed)
            calls.append(kw)
            if raises: raise TimeoutError()
            return {'rt_cd':'0','output':{'ODNO':'fixture'}}
        repo=SimpleNamespace(pool=SimpleNamespace(connection=lambda:nullcontext(conn)),
            record_response=lambda *a:responses.append(a),cancel_response=lambda *a:responses.append(a))
        adapter=FlowTransport(repo,SimpleNamespace(post_once=post),SimpleNamespace(cano='fake',account_product_code='fake'))
        adapter.cash_check=lambda *a:cash_reason
        with patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':'Y'}):
            first=adapter.run()
            self.assertEqual(adapter.run(),0)  # Restart/reprocess same durable claim.
        return first,calls,responses

    def test_once_and_timeout_never_resend(self):
        for raises in (False,True):
            first,calls,responses=self.run_fake(Connection(),raises)
            self.assertEqual((first,len(calls),len(responses)),(1,1,1))
            if raises: self.assertEqual(responses[0][1],{})

    def test_cancel_once(self):
        first,calls,_=self.run_fake(Connection(cancel=True))
        self.assertEqual(first,1)
        self.assertEqual(calls[0]['payload']['ORD_QTY'],'2')

    def test_env_y_db_n_zero_http(self):
        first,calls,_=self.run_fake(Connection(db='N'))
        self.assertEqual((first,len(calls)),(0,0))

    def test_no_arming_intents_before_approval(self):
        from datetime import datetime,timezone,timedelta
        approved=datetime(2026,9,10,tzinfo=timezone.utc)
        query=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchone=lambda:('Y',approved)))
        with patch.dict('os.environ',{'FLOW_V3_ACTUAL_SEND':'Y'}):
            self.assertFalse(send_authorized(query,approved-timedelta(seconds=1)))
            self.assertTrue(send_authorized(query,approved+timedelta(seconds=1)))

    def test_operator_install_targets_only_flow(self):
        from scripts.ops.flow_v3_send_approval import install_gate
        with patch('scripts.ops.flow_v3_send_approval.run') as fake:
            install_gate('Y')  # Fake systemctl/install only; no deployment/config change.
        commands=fake.call_args_list
        self.assertEqual(len(commands),3)
        self.assertIn('trading-flow-v3-live-nosend.service.d',str(commands))
        self.assertNotIn('minute',str(commands).lower())
        self.assertNotIn('daily',str(commands).lower())

    def test_revoked_after_claim_zero_http(self):
        conn=Connection(revoke=True)
        first,calls,_=self.run_fake(conn)
        self.assertEqual((first,len(calls)),(0,0))
        self.assertTrue(any('post_attempt_count=0' in sql for sql,_ in conn.statements))

    def test_invalid_strategy_product_side_quantity_or_payload_zero_http(self):
        for index,value in ((2,''),(3,'005930'),(5,'0197X0'),(6,'INVALID'),(7,0)):
            row=list(fixture());row[index]=value
            first,calls,_=self.run_fake(Connection(row=tuple(row)))
            self.assertEqual((first,len(calls)),(0,0))
        row=list(fixture());row[1]=request_payload('0193T0','BUY',6)
        self.assertRaises(ValueError,validate_claim,tuple(row),False)

    def test_final_db_read_failure_no_http(self):
        class Broken(Connection):
            def execute(self,sql,args=None):
                if 'SELECT enabled' in sql and self.claimed: raise ConnectionError('fixture')
                return super().execute(sql,args)
        self.assertRaises(ConnectionError,self.run_fake,Broken())

    def test_all_routes_cash_recheck_denies_post_and_never_retries(self):
        for route,code,direction in [('UNDERLYING','000660','LONG'),('LEVERAGE','0193T0','LONG'),('INVERSE','0197X0','SHORT')]:
            row=list(fixture());row[1]=request_payload(code,'BUY',5)
            row[4]=direction;row[5]=code;row[10]=route
            conn=Connection(row=tuple(row))
            first,calls,_=self.run_fake(conn,cash_reason='KIS_ORDERABLE_CASH_INSUFFICIENT')
            self.assertEqual((first,len(calls)),(0,0))
            self.assertTrue(any(args and 'KIS_ORDERABLE_CASH_INSUFFICIENT' in args for _,args in conn.statements))

    def test_leverage_inverse_authorized_path_not_unconditionally_blocked(self):
        for route,code,direction in [('LEVERAGE','0193T0','LONG'),('INVERSE','0197X0','SHORT')]:
            row=list(fixture());row[1]=request_payload(code,'BUY',5)
            row[4]=direction;row[5]=code;row[10]=route
            first,calls,_=self.run_fake(Connection(row=tuple(row)))
            self.assertEqual((first,len(calls)),(1,1))
