from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class DashboardOptionalSeriesContractTest(unittest.TestCase):
    def test_json_contract_has_empty_series_statuses(self):
        source = (ROOT / "scripts" / "dashboard" / "serve_multi_ma_dashboard.py").read_text(encoding="utf-8")
        self.assertIn('"programStatus": "NORMAL" if program_rows else "DATA_MISSING"', source)
        self.assertIn('"executionStrengthStatus": "NORMAL" if execution_rows else "DATA_MISSING"', source)

    def test_client_normalizes_missing_optional_arrays_before_length(self):
        html = (ROOT / "reports" / "multi-ma" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Array.isArray(data[key])", html)
        self.assertIn("programMinuteSeries.length", html)
        self.assertIn("executionStrengthSeries.length", html)
