from pathlib import Path
import unittest


ROOT=Path(__file__).resolve().parents[1]


class MinuteMaContractTest(unittest.TestCase):
    def test_additive_schema_has_exact_axes_and_send_locked(self):
        sql=(ROOT/"database/migrations/20260826_minute_ma_v01_additive.sql").read_text(encoding="utf-8")
        for axis in ("KRX_CONTINUOUS","KRX_RESET","INTEGRATED_CONTINUOUS","INTEGRATED_RESET"):
            self.assertIn(axis,sql)
        self.assertIn("strategies<>2400 OR paths<>9600 OR operations<>9600",sql)
        self.assertIn("'MINUTE_MA_LIVE_SEND','N'",sql)
        self.assertNotIn("DELETE FROM daily_strategy",sql.upper())

    def test_shared_execution_is_reused(self):
        source=(ROOT/"src/minute_ma/live_nosend.py").read_text(encoding="utf-8")
        self.assertIn("INSERT INTO live_order_request",source)
        self.assertIn("execution_reconciliation_audit",source)
        self.assertNotIn("submit(",source)

    def test_paper_and_no_send_runners_have_no_submit_transport(self):
        for name in ("run_minute_ma_paper.py","run_minute_ma_live_nosend.py"):
            source=(ROOT/"scripts/runtime"/name).read_text(encoding="utf-8")
            self.assertNotIn("KISOrderPostTransport",source)
            self.assertNotIn(".submit(",source)

    def test_dashboard_route_and_four_axis_page_exist(self):
        server=(ROOT/"scripts/dashboard/serve_multi_ma_dashboard.py").read_text(encoding="utf-8")
        page=(ROOT/"reports/multi-ma/minute-ma.html").read_text(encoding="utf-8")
        self.assertIn('/minute-ma/api/dashboard',server)
        self.assertIn('/minute-ma/api/detail',server)
        self.assertIn('KRX_CONTINUOUS',page)
        self.assertIn('INTEGRATED_RESET',page)

    def test_afternoon_schema_is_additive_and_send_stays_locked(self):
        sql=(ROOT/"database/migrations/20260826_minute_ma_afternoon_additive.sql").read_text(encoding="utf-8")
        for axis in (
            "KRX_CONTINUOUS_AFTERNOON", "KRX_RESET_AFTERNOON",
            "INTEGRATED_CONTINUOUS_AFTERNOON", "INTEGRATED_RESET_AFTERNOON",
        ):
            self.assertIn(axis,sql)
        self.assertIn("path_count <> 19200",sql)
        self.assertIn("send_enabled",sql)
        self.assertNotIn("DELETE FROM",sql.upper())


if __name__=="__main__": unittest.main()
