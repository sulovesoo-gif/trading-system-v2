from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import unittest

from src.service.research_video_dashboard_service import event_analysis_payload, performance_payload, runs_payload


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
        row = ("id", "created", "start", "end", "COMPLETED", {"ablation": "FULL"}, 7, 3, 20, 10, 2, 8)
        payload = runs_payload(_Pool([row]))
        self.assertEqual(payload["runs"][0]["cycle_count"], 3)
        self.assertEqual(payload["runs"][0]["net_profit"], 8)

    def test_dashboard_is_korean_and_contains_required_sections(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        for label in ("실행 결과 선택", "실행 설명", "1분봉 캔들 및 이벤트", "선택 캔들·이벤트 판단 근거", "실행 전체 성과", "실행안 / 조건값 비교", "가격 보간"):
            self.assertIn(label, page)
        for variant in ("구조 조건 제외", "캔들 몸통 조건 제외", "거래량 조건 제외", "꼬리 조건 제외"):
            self.assertIn(variant, page)

    def test_performance_groups_stored_cycles_without_replay(self):
        row = (1,date(2026,8,3),"000660","LONG",datetime(2026,8,3,10),Decimal("100"),
               datetime(2026,8,3,10,5),Decimal("110"),"STOP",300,Decimal("10"),Decimal("1"),
               Decimal("9"),Decimal("0.9"),Decimal("0.02"),Decimal("-0.01"))
        payload = performance_payload(_Pool([row]), "09967576-c8dc-4161-9d54-5b8f5e8e60a2", "day", "000660")
        self.assertEqual(payload["items"][0]["period"], "2026-08-03")
        self.assertEqual(payload["items"][0]["net_profit"], Decimal("9"))
        self.assertEqual(payload["items"][0]["trade_count"], 1)

    def test_replay_units_and_performance_links_exist(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        for label in ("일별 재생", "거래 재생", "이전 거래일", "다음 거래일", "내부 판단 이벤트 표시", "집계 단위", "최대 낙폭"):
            self.assertIn(label, page)
        self.assertIn("performanceReplay", page)

    def test_broker_chart_navigation_contract(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        for value in ("30", "60", "90", "120", "300", "600"):
            self.assertIn(f">{value}</option>", page)
        for contract in ("navigator", "moveCrosshair", "installDrag", "entryJump", "exitJump", "전체 로드", "현재 viewport"):
            self.assertIn(contract, page)

    def test_human_validation_sections_and_derived_structure_exist(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        for label in ("선택 거래", "동일 신호 실행상품 비교", "조건 충족 판단", "Pivot 발생", "confirmed_time 이전", "확정규칙", "설정값", "비교시험"):
            self.assertIn(label, page)
        for contract in ("structureLabels", "renderSelectedTrade", "renderProjection", "Signal Source: 000660"):
            self.assertIn(contract, page)

    def test_sma_path_restarts_after_null_warmup_and_markers_are_human_readable(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        for contract in ("let maStarted=false", "const command=maStarted?'L':'M'", "관련 이벤트 ${items.length}건"):
            self.assertIn(contract, page)
        for label in ("고점", "저점", "고↑", "고↓", "저↑", "저↓"):
            self.assertIn(label, page)


if __name__ == "__main__":
    unittest.main()
