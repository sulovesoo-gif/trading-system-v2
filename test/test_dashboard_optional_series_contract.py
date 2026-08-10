from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).parents[1]


class DashboardOptionalSeriesContractTest(unittest.TestCase):
    def test_all_embedded_javascript_parses_with_node(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        check = "const fs=require('fs');const h=fs.readFileSync(process.argv[1],'utf8');for(const b of h.matchAll(/<script>([\\s\\S]*?)<\\/script>/g)){new Function(b[1])}"
        subprocess.run([node, "-e", check, str(ROOT / "reports" / "multi-ma" / "index.html")], check=True)

    def test_research_performance_client_parses_with_node_and_has_read_only_filters(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        page = ROOT / "reports" / "multi-ma" / "research-performance.html"
        check = "const fs=require('fs');const h=fs.readFileSync(process.argv[1],'utf8');for(const b of h.matchAll(/<script>([\\s\\S]*?)<\\/script>/g)){new Function(b[1])}"
        subprocess.run([node, "-e", check, str(page)], check=True)
        html = page.read_text(encoding="utf-8")
        self.assertIn('id="session"', html)
        self.assertIn("MA10_READY_AT_SIGNAL", html)
        self.assertIn("start_date", html)
        self.assertIn("end_date", html)
    def test_json_contract_has_empty_series_statuses(self):
        source = (ROOT / "scripts" / "dashboard" / "serve_multi_ma_dashboard.py").read_text(encoding="utf-8")
        self.assertIn('"programStatus": "NORMAL" if program_rows else "DATA_MISSING"', source)
        self.assertIn('"executionStrengthStatus": "NORMAL" if execution_rows else "DATA_MISSING"', source)

    def test_client_normalizes_missing_optional_arrays_before_length(self):
        html = (ROOT / "reports" / "multi-ma" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Array.isArray(data[key])", html)
        self.assertIn("programMinuteSeries.length", html)
        self.assertIn("executionStrengthSeries.length", html)
