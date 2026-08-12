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
        for variant in ("구조 조건 제외형", "몸통 조건 제외형", "거래량 조건 제외형", "꼬리 경고 제외형"):
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
        for label in ("선택 거래", "동일 신호 실행상품 비교", "진입 조건 판단", "피벗 발생", "확정 시각 이전", "확정 규칙", "설정값", "비교 시험"):
            self.assertIn(label, page)
        for contract in ("structureLabels", "renderSelectedTrade", "renderProjection", "신호 원천: SK하이닉스(000660)"):
            self.assertIn(contract, page)

    def test_sma_path_restarts_after_null_warmup_and_markers_are_human_readable(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        for contract in ("let maStarted=false", "const command=maStarted?'L':'M'", "관련 이벤트 ${items.length}건"):
            self.assertIn(contract, page)
        for label in ("고점", "저점", "고점↑", "고점↓", "저점↑", "저점↓"):
            self.assertIn(label, page)

    def test_trade_audit_is_decision_first_and_fully_korean(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        for label in ("필수조건", "선택조건", "현재 실행 규칙 판단", "영상 원형 검토",
                      "청산 조건 판단", "진입 → 보유 → 경고 → 청산", "최대 유리 변동",
                      "최대 불리 변동", "이벤트 상세 / 내부 디버그"):
            self.assertIn(label, page)
        for contract in ("importantStructures", "진입 판단 직전", "tradeFlow", "exitJudgment",
                         "실제 청산 손익", "가격 누락 · 보간 없음"):
            self.assertIn(contract, page)

    def test_volume_chart_reuses_replay_viewport_and_stored_features(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        for label in ("1분봉 거래량", "20분 평균 거래량", "평균 대비", "거래량 변화율", "거래량 기울기"):
            self.assertIn(label, page)
        for contract in ("volumeChart", "renderVolumeChart(fs)", "details[i].volume_avg", "d.volume_ratio",
                         "installDrag(svg,fs)", "selectedTime", "renderVolumeChart(visible())"):
            self.assertIn(contract, page)

    def test_replay_chart_uses_korean_decision_markers_and_cycle_reference_lines(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        for label in ("고점↑", "고점↓", "저점↑", "저점↓", "눌림", "재회복",
                      "진입 준비▲", "하락 준비▼", "매수▲", "하락 진입▼",
                      "청산▼", "손절×", "반전 경고!", "직전 고점", "직전 저점"):
            self.assertIn(label, page)
        for contract in ("chartEventVisible", "chartMarkerText", "structureContext",
                         "referenceLines", "structureLinks", "bodyComparison"):
            self.assertIn(contract, page)
        self.assertIn("SMA_PULLBACK','SMA_RECLAIM", page)

    def test_body_comparison_does_not_guess_an_unstored_source_bar(self):
        page = (Path(__file__).parents[1] / "reports/multi-ma/research-video-strategy.html").read_text(encoding="utf-8")
        self.assertIn("BODY_EXP_PREVIOUS", page)
        self.assertIn("BODY_EXP_PREVIOUS_SAME_DIRECTION", page)
        self.assertIn("현재 저장값으로 원본 봉 특정 불가", page)
        self.assertIn("비교 몸통", page)
        self.assertIn("진입봉 몸통", page)

    def test_execution_volume_profile_limit_is_documented(self):
        doc = (Path(__file__).parents[1] / "docs/VIDEO_STRATEGY.md").read_text(encoding="utf-8")
        for label in ("완전한 체결 tape", "완전한 매물대", "공식 Volume Profile", "추정 체결 분포", "APPROXIMATE"):
            self.assertIn(label, doc)


if __name__ == "__main__":
    unittest.main()
