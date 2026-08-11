from pathlib import Path
import unittest

from src.service.research_video_dashboard_service import event_analysis_payload, runs_payload


class _Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed = []

    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, params=None): self.executed.append((sql, params))
    def fetchall(self): return self.rows


class _Connection:
    def __init__(self, cursor): self._cursor = cursor
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self): return self._cursor


class _Pool:
    def __init__(self, rows=()): self.cursor = _Cursor(rows)
    def connection(self): return _Connection(self.cursor)


class VideoDashboardTest(unittest.TestCase):
    def test_events_without_run_id_returns_clear_error(self):
        payload = event_analysis_payload(_Pool(), "")
        self.assertEqual(payload["status"], "ERROR")
        self.assertEqual(payload["message"], "run_id가 필요합니다.")

    def test_run_summary_contains_profit_and_counts(self):
        row = ("id", "created", "start", "end", "COMPLETED", {"ablation": "FULL"}, 7, 3, 10, 2, 8)
        payload = runs_payload(_Pool([row]))
        self.assertEqual(payload["runs"][0]["cycle_count"], 3)
        self.assertEqual(payload["runs"][0]["net_profit"], 8)

    def test_dashboard_is_korean_and_contains_required_sections(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        for label in ("실행 결과 선택", "실행 설명", "1분봉 캔들 및 이벤트", "선택 캔들·이벤트 판단 근거", "종목별 성과 요약", "Variant 비교", "가격 보간"):
            self.assertIn(label, page)
        for variant in ("구조 조건 제외", "캔들 몸통 조건 제외", "거래량 조건 제외", "꼬리 조건 제외"):
            self.assertIn(variant, page)


if __name__ == "__main__":
    unittest.main()
