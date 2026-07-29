"""ntfy HTTP 알림 서비스. 주문·DB 저장 기능을 포함하지 않는다."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class NtfySettings:
    base_url: str
    topic: str
    priority: str

    @classmethod
    def from_environment(cls) -> "NtfySettings":
        if os.getenv("ALERT_NTFY_ENABLED", "false").lower() != "true":
            raise RuntimeError("ALERT_NTFY_ENABLED=true 설정이 필요합니다.")
        base_url = os.getenv("ALERT_NTFY_BASE_URL", "").rstrip("/")
        topic = os.getenv("ALERT_NTFY_TOPIC", "")
        priority = os.getenv("ALERT_NTFY_PRIORITY", "high").lower()
        if not base_url or not topic:
            raise RuntimeError("ntfy 알림 환경 변수가 없습니다: ALERT_NTFY_BASE_URL, ALERT_NTFY_TOPIC")
        if priority not in {"min", "low", "default", "high", "max"}:
            raise RuntimeError("ALERT_NTFY_PRIORITY 값이 올바르지 않습니다.")
        return cls(base_url=base_url, topic=topic, priority=priority)


class NtfyAlertService:
    def __init__(self, settings: NtfySettings, *, opener=urlopen) -> None:
        self.settings = settings
        self._opener = opener

    def send(self, *, subject: str, body: str) -> None:
        payload = json.dumps(
            {"topic": self.settings.topic, "title": subject, "message": body, "priority": self.settings.priority},
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{self.settings.base_url}/{self.settings.topic}", data=payload, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with self._opener(request, timeout=15) as response:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if status < 200 or status >= 300:
                raise RuntimeError(f"ntfy 알림 전송 실패: HTTP {status}")
