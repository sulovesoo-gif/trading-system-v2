"""KIS REST 응답의 HTTP 및 업무 오류를 함께 검증하는 클라이언트."""

from __future__ import annotations

import os
import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .kis_auth import KISAuth, KISAuthError


class KISClientError(RuntimeError):
    """KIS HTTP 또는 업무 응답 오류."""


class _UrlLibResponse:
    def __init__(self, response) -> None:
        self.response = response

    def raise_for_status(self) -> None:
        return None

    @property
    def status_code(self) -> int | None:
        return getattr(self.response, "status", None)

    def json(self) -> dict[str, Any]:
        return json.loads(self.response.read().decode("utf-8"))


class _UrlLibSession:
    """requests 없이도 동작하는 최소 GET 어댑터."""

    def get(self, url: str, *, headers: dict, params: dict, timeout: int):
        query = urlencode(params)
        request = Request(f"{url}?{query}" if query else url, headers=headers, method="GET")
        try:
            return _UrlLibResponse(urlopen(request, timeout=timeout))
        except HTTPError as error:
            raise KISClientError(f"KIS HTTP 오류: {error.code}") from error


class KISClient:
    TOKEN_EXPIRED_CODES = {"EGW00121", "EGW00123"}
    def __init__(
        self,
        *,
        auth: KISAuth | None = None,
        base_url: str | None = None,
        app_key: str | None = None,
        app_secret: str | None = None,
        session=None,
        timeout: int = 10,
    ) -> None:
        self.base_url = (base_url or os.getenv("KIS_BASE_URL") or "").rstrip("/")
        self.app_key = app_key or os.getenv("KIS_API_KEY") or ""
        self.app_secret = app_secret or os.getenv("KIS_API_SECRET") or ""
        self.auth = auth or KISAuth(
            base_url=self.base_url, app_key=self.app_key, app_secret=self.app_secret
        )
        self.session = session or _UrlLibSession()
        self.timeout = timeout
        self.last_payload: dict[str, Any] | None = None
        self.last_response_headers: dict[str, str] = {}
        self.last_http_status: int | None = None

    def get(
        self, *, path: str, tr_id: str, params: dict[str, str], custtype: str = "P"
    ) -> dict[str, Any]:
        if not self.base_url or not self.app_key or not self.app_secret:
            raise KISClientError("KIS_BASE_URL, KIS_API_KEY, KIS_API_SECRET를 확인하세요.")
        self.last_payload = None
        self.last_response_headers = {}
        self.last_http_status = None
        for attempt in range(2):
            try:
                token = self.auth.get_token()
            except KISAuthError:
                raise
            try:
                response = self.session.get(
                    f"{self.base_url}{path}",
                    headers={
                        "content-type": "application/json",
                        "authorization": f"Bearer {token}",
                        "appkey": self.app_key,
                        "appsecret": self.app_secret,
                        "tr_id": tr_id,
                        "custtype": custtype,
                    },
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                self.last_http_status = getattr(response, "status_code", getattr(response, "status", None))
            except Exception as error:
                # HTTP 계층 오류는 KIS 업무 오류와 분리하며, 토큰·비밀값은 노출하지 않는다.
                raise KISClientError(f"KIS HTTP 요청 오류: {type(error).__name__}") from error
            try:
                payload = response.json()
            except ValueError as error:
                raise KISClientError("KIS 응답이 JSON 형식이 아닙니다.") from error
            if not isinstance(payload, dict):
                raise KISClientError("KIS 응답 최상위 객체가 JSON 객체가 아닙니다.")
            self.last_payload = payload
            headers = getattr(response, "headers", {})
            self.last_response_headers = {str(key).lower(): str(value) for key, value in dict(headers).items()}
            if payload.get("rt_cd") == "0":
                return payload
            if payload.get("msg_cd") in self.TOKEN_EXPIRED_CODES and attempt == 0:
                self.auth.invalidate_token()
                continue
            raise KISClientError(
                f"KIS 업무 오류: {payload.get('msg_cd', 'UNKNOWN')} {payload.get('msg1', '')}".strip()
            )
        raise KISClientError("KIS 토큰 재발급 후에도 요청에 실패했습니다.")
