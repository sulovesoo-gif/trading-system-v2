"""주문 기능 없이 SMTP 이메일만 전송하는 알림 서비스."""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class EmailSettings:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str
    starttls: bool

    @classmethod
    def from_environment(cls) -> "EmailSettings":
        values = {name: os.getenv(name, "") for name in (
            "ALERT_SMTP_HOST", "ALERT_SMTP_USERNAME", "ALERT_SMTP_PASSWORD", "ALERT_EMAIL_FROM", "ALERT_EMAIL_TO"
        )}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise RuntimeError("이메일 알림 환경 변수가 없습니다: " + ", ".join(missing))
        return cls(values["ALERT_SMTP_HOST"], int(os.getenv("ALERT_SMTP_PORT", "587")), values["ALERT_SMTP_USERNAME"],
                   values["ALERT_SMTP_PASSWORD"], values["ALERT_EMAIL_FROM"], values["ALERT_EMAIL_TO"],
                   os.getenv("ALERT_SMTP_STARTTLS", "Y").upper() == "Y")


class EmailAlertService:
    def __init__(self, settings: EmailSettings, *, smtp_factory=smtplib.SMTP) -> None:
        self.settings = settings
        self._smtp_factory = smtp_factory

    def send(self, *, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.sender
        message["To"] = self.settings.recipient
        message.set_content(body)
        with self._smtp_factory(self.settings.host, self.settings.port, timeout=15) as client:
            if self.settings.starttls:
                client.starttls()
            client.login(self.settings.username, self.settings.password)
            client.send_message(message)
