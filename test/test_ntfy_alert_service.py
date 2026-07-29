from __future__ import annotations

import json
import os
import unittest

from src.service.ntfy_alert_service import NtfyAlertService, NtfySettings


class FakeResponse:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *_): return False


class NtfyAlertServiceTest(unittest.TestCase):
    def test_json_notification_contains_topic_title_and_message(self):
        requests = []
        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse()
        service = NtfyAlertService(NtfySettings("https://ntfy.sh", "topic", "high"), opener=opener)
        service.send(subject="테스트", body="주문 없음")
        request, timeout = requests[0]
        self.assertEqual((request.full_url, timeout), ("https://ntfy.sh/topic", 15))
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"topic": "topic", "title": "테스트", "message": "주문 없음", "priority": "high"})

    def test_settings_requires_enabled_and_topic(self):
        old = dict(os.environ)
        try:
            os.environ["ALERT_NTFY_ENABLED"] = "false"
            with self.assertRaises(RuntimeError):
                NtfySettings.from_environment()
        finally:
            os.environ.clear(); os.environ.update(old)
