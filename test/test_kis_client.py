import unittest

from src.collector.raw.kis_client import KISClient, KISClientError
from src.collector.raw.kis_auth import KISAuth


class Response:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class Session:
    def __init__(self, payload): self.payload = payload
    def get(self, *args, **kwargs): return Response(self.payload)


class SequenceSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []
    def get(self, *args, **kwargs):
        self.calls.append(kwargs)
        return Response(self.payloads.pop(0))


class TokenSession:
    def __init__(self): self.calls = 0
    def post(self, *args, **kwargs):
        self.calls += 1
        return Response({"access_token": "refreshed-token", "expires_in": "3600"})


class Auth:
    def __init__(self): self.invalidated = 0
    def get_token(self): return "token"
    def invalidate_token(self): self.invalidated += 1


class NoopTokenCache:
    def load(self): return None
    def save(self, **kwargs): pass
    def discard(self): pass
    class _Lock:
        def __enter__(self): return None
        def __exit__(self, *args): return False
    def refresh_lock(self): return self._Lock()


class KISClientTest(unittest.TestCase):
    def test_rt_cd_error_raises(self):
        client = KISClient(base_url="https://example.test", app_key="key", app_secret="secret", auth=Auth(), session=Session({"rt_cd": "1", "msg_cd": "E", "msg1": "실패"}))
        with self.assertRaises(KISClientError):
            client.get(path="/x", tr_id="TEST", params={})

    def test_success_returns_payload(self):
        client = KISClient(base_url="https://example.test", app_key="key", app_secret="secret", auth=Auth(), session=Session({"rt_cd": "0", "output": {}}))
        self.assertEqual(client.get(path="/x", tr_id="TEST", params={})["rt_cd"], "0")

    def test_expired_token_reissues_and_retries_once(self):
        auth = Auth()
        session = SequenceSession([
            {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."},
            {"rt_cd": "0", "output": {}},
        ])
        client = KISClient(base_url="https://example.test", app_key="key", app_secret="secret", auth=auth, session=session)
        self.assertEqual(client.get(path="/x", tr_id="TEST", params={})["rt_cd"], "0")
        self.assertEqual(auth.invalidated, 1)
        self.assertEqual(len(session.calls), 2)

    def test_expired_token_uses_reissued_token_for_one_retry(self):
        token_session = TokenSession()
        auth = KISAuth(
            base_url="https://example.test", app_key="key", app_secret="secret", session=token_session, token_cache=NoopTokenCache()
        )
        auth.access_token = "expired-token"
        auth.expires_at = auth._as_kst(auth._now()).replace(year=2099)
        session = SequenceSession([
            {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "만료"},
            {"rt_cd": "0", "output": {}},
        ])
        client = KISClient(base_url="https://example.test", app_key="key", app_secret="secret", auth=auth, session=session)
        client.get(path="/x", tr_id="TEST", params={})
        self.assertEqual(token_session.calls, 1)
        self.assertEqual(session.calls[0]["headers"]["authorization"], "Bearer expired-token")
        self.assertEqual(session.calls[1]["headers"]["authorization"], "Bearer refreshed-token")

    def test_expired_token_second_failure_does_not_retry_forever(self):
        auth = Auth()
        session = SequenceSession([
            {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "만료"},
            {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "만료"},
        ])
        client = KISClient(base_url="https://example.test", app_key="key", app_secret="secret", auth=auth, session=session)
        with self.assertRaises(KISClientError):
            client.get(path="/x", tr_id="TEST", params={})
        self.assertEqual(auth.invalidated, 1)
        self.assertEqual(len(session.calls), 2)

    def test_non_json_response_fails_without_secret(self):
        class NonJson(Response):
            def json(self): raise ValueError("not json")
        class NonJsonSession:
            def get(self, *args, **kwargs): return NonJson({})
        client = KISClient(base_url="https://example.test", app_key="key", app_secret="TOP_SECRET", auth=Auth(), session=NonJsonSession())
        with self.assertRaises(KISClientError) as context:
            client.get(path="/x", tr_id="TEST", params={})
        self.assertNotIn("TOP_SECRET", str(context.exception))
