from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.collector.raw.kis_auth import KISAuth
from src.collector.raw.token_cache import TokenCache


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 7, 29, 10, 0, tzinfo=KST)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class TokenSession:
    def __init__(self):
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        return Response({"access_token": "issued-token", "expires_in": "3600"})


class TokenCacheTest(unittest.TestCase):
    def test_cache_writes_atomically_and_reuses_valid_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "kis_token.json"
            cache = TokenCache(path)
            cache.save(access_token="cached-token", expires_at=NOW + timedelta(hours=1))
            self.assertTrue(path.exists())
            cached_payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(cached_payload), {"access_token", "expires_at"})
            self.assertEqual(cached_payload["access_token"], "cached-token")
            auth = KISAuth(base_url="https://example.test", app_key="key", app_secret="secret", session=TokenSession(), now_provider=lambda: NOW, token_cache=cache)
            self.assertEqual(auth.get_token(), "cached-token")
            self.assertEqual(auth.session.calls, 0)

    def test_corrupt_or_incomplete_cache_is_discarded_and_token_is_issued(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kis_token.json"
            path.write_text('{"access_token": "broken"}', encoding="utf-8")
            session = TokenSession()
            auth = KISAuth(base_url="https://example.test", app_key="key", app_secret="secret", session=session, now_provider=lambda: NOW, token_cache=TokenCache(path))
            self.assertEqual(auth.get_token(), "issued-token")
            self.assertEqual(session.calls, 1)
            self.assertTrue(path.exists())

    def test_safety_window_does_not_reuse_near_expiry_token(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = TokenCache(Path(directory) / "kis_token.json")
            cache.save(access_token="near-expiry", expires_at=NOW + timedelta(seconds=KISAuth.TOKEN_SAFETY_SECONDS))
            session = TokenSession()
            auth = KISAuth(base_url="https://example.test", app_key="key", app_secret="secret", session=session, now_provider=lambda: NOW, token_cache=cache)
            self.assertEqual(auth.get_token(), "issued-token")
            self.assertEqual(session.calls, 1)

    def test_kis_absolute_expiration_is_parsed_as_kst(self):
        class AbsoluteExpirySession(TokenSession):
            def post(self, *args, **kwargs):
                self.calls += 1
                return Response({"access_token": "issued-token", "access_token_token_expired": "2026-07-29 12:00:00"})

        with tempfile.TemporaryDirectory() as directory:
            auth = KISAuth(base_url="https://example.test", app_key="key", app_secret="secret", session=AbsoluteExpirySession(), now_provider=lambda: NOW, token_cache=TokenCache(Path(directory) / "kis_token.json"))
            auth.get_token()
            self.assertEqual(auth.expires_at, datetime(2026, 7, 29, 12, 0, tzinfo=KST))

    def test_invalidate_removes_local_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kis_token.json"
            cache = TokenCache(path)
            cache.save(access_token="cached-token", expires_at=NOW + timedelta(hours=1))
            auth = KISAuth(base_url="https://example.test", app_key="key", app_secret="secret", session=TokenSession(), now_provider=lambda: NOW, token_cache=cache)
            auth.invalidate_token()
            self.assertFalse(path.exists())
