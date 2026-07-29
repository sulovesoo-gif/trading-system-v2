from __future__ import annotations

import unittest

from src.service.email_alert_service import EmailAlertService, EmailSettings


class FakeSmtp:
    sent = None
    def __init__(self, *_args, **_kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def starttls(self): pass
    def login(self, *_): pass
    def send_message(self, message): FakeSmtp.sent = message


class EmailAlertServiceTest(unittest.TestCase):
    def test_email_body_is_sent_without_exposing_settings(self):
        service = EmailAlertService(EmailSettings("smtp.example", 587, "user", "secret", "from@example", "to@example", True), smtp_factory=FakeSmtp)
        service.send(subject="[Trading V2] LONG", body="candidate")
        self.assertEqual(FakeSmtp.sent["Subject"], "[Trading V2] LONG")
        self.assertIn("candidate", FakeSmtp.sent.get_content())
