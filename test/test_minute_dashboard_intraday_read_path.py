from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MinuteDashboardIntradayReadPathTest(unittest.TestCase):
    def test_read_service_exposes_strategy_and_period_lifecycle_counts(self):
        source = (ROOT / "src/service/minute_ma_dashboard_service.py").read_text(encoding="utf-8")
        self.assertIn("today_live_entry_strategies", source)
        self.assertIn("today_live_exit_strategies", source)
        self.assertIn('"TODAY_LIVE_EXIT"', source)
        for field in (
            "period_paper_entry_count", "period_paper_exit_count", "period_paper_open_count",
            "period_live_entry_count", "period_live_exit_count", "period_live_open_count",
        ):
            self.assertIn(field, source)

    def test_ui_keeps_realized_metrics_and_adds_korean_clickable_lifecycle(self):
        page = (ROOT / "reports/multi-ma/minute-ma.html").read_text(encoding="utf-8")
        self.assertEqual(page.count("filterCard('미청산 PAPER'"), 1)
        self.assertIn("기간 Lifecycle", page)
        self.assertIn("'전송시도'", page)
        self.assertIn("'접수'", page)
        self.assertIn("'거절'", page)
        self.assertIn("'미확인'", page)
        self.assertIn("'체결'", page)
        self.assertIn("filterCard('실주문전송',o.send_enabled,'POST'", page)
        self.assertIn("today_live_entry+'타점 / '+o.today_live_entry_strategies+'전략'", page)


if __name__ == "__main__":
    unittest.main()
