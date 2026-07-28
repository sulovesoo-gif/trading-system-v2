"""KIS REST access token issuance, reuse, and local-cache handling."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .token_cache import TokenCache

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False


KST = ZoneInfo("Asia/Seoul")


class KISAuthError(RuntimeError):
    """KIS authentication configuration or token issuance failed."""


class _UrlLibResponse:
    def __init__(self, response) -> None:
        self.response = response

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return json.loads(self.response.read().decode("utf-8"))


class _UrlLibSession:
    def post(self, url: str, *, headers: dict, json: dict, timeout: int):
        request = Request(url, data=__import__("json").dumps(json).encode("utf-8"), headers=headers, method="POST")
        try:
            return _UrlLibResponse(urlopen(request, timeout=timeout))
        except HTTPError as error:
            raise KISAuthError(f"KIS token HTTP error: {error.code}") from error


class KISAuth:
    TOKEN_PATH = "/oauth2/tokenP"
    TOKEN_SAFETY_SECONDS = 30

    def __init__(
        self,
        *,
        base_url: str | None = None,
        app_key: str | None = None,
        app_secret: str | None = None,
        session=None,
        now_provider: Callable[[], datetime] | None = None,
        token_cache: TokenCache | None = None,
        token_cache_path: str | None = None,
    ) -> None:
        load_dotenv()
        self.base_url = (base_url or os.getenv("KIS_BASE_URL") or "").rstrip("/")
        self.app_key = app_key or os.getenv("KIS_API_KEY") or ""
        self.app_secret = app_secret or os.getenv("KIS_API_SECRET") or ""
        self.session = session or _UrlLibSession()
        self._now = now_provider or (lambda: datetime.now(KST))
        cache_path = token_cache_path or os.getenv("KIS_TOKEN_CACHE_PATH") or ".cache/kis_token.json"
        self.token_cache = token_cache or TokenCache(cache_path)
        self.access_token: str | None = None
        self.expires_at: datetime | None = None

    def _validate_configuration(self) -> None:
        missing = [name for name, value in {"KIS_BASE_URL": self.base_url, "KIS_API_KEY": self.app_key, "KIS_API_SECRET": self.app_secret}.items() if not value]
        if missing:
            raise KISAuthError(f"Required KIS environment variables are missing: {', '.join(missing)}")

    def _issue_token_from_api(self) -> str:
        self._validate_configuration()
        response = self.session.post(
            f"{self.base_url}{self.TOKEN_PATH}",
            headers={"content-type": "application/json"},
            json={"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret},
            timeout=10,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise KISAuthError("KIS token response is not JSON.") from error
        if not isinstance(payload, dict):
            raise KISAuthError("KIS token response top level is not an object.")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise KISAuthError(f"KIS token issuance failed: {payload.get('msg_cd', 'UNKNOWN')}")
        now = self._as_kst(self._now())
        expires_at = self._parse_expiration(payload.get("access_token_token_expired"))
        if expires_at is None:
            try:
                seconds = int(payload.get("expires_in", 0))
            except (TypeError, ValueError):
                seconds = 0
            expires_at = now + timedelta(seconds=max(seconds, 0))
        self.access_token = token
        self.expires_at = expires_at
        return token

    def issue_token(self) -> str:
        token = self._issue_token_from_api()
        self.token_cache.save(access_token=token, expires_at=self.expires_at)
        return token

    def get_token(self) -> str:
        now = self._as_kst(self._now())
        if self._memory_token_is_valid(now):
            return self.access_token  # type: ignore[return-value]
        cached = self.token_cache.load()
        if cached and self._expires_safely_after(cached[1], now):
            self.access_token, self.expires_at = cached
            return self.access_token
        with self.token_cache.refresh_lock():
            cached = self.token_cache.load()
            if cached and self._expires_safely_after(cached[1], now):
                self.access_token, self.expires_at = cached
                return self.access_token
            return self.issue_token()

    def invalidate_token(self) -> None:
        self.access_token = None
        self.expires_at = None
        self.token_cache.discard()

    def _memory_token_is_valid(self, now: datetime) -> bool:
        return bool(self.access_token and self.expires_at and self._expires_safely_after(self.expires_at, now))

    def _expires_safely_after(self, expires_at: datetime, now: datetime) -> bool:
        return now + timedelta(seconds=self.TOKEN_SAFETY_SECONDS) < expires_at

    @staticmethod
    def _parse_expiration(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return KISAuth._as_kst(parsed)

    @staticmethod
    def _as_kst(value: datetime) -> datetime:
        return value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)
