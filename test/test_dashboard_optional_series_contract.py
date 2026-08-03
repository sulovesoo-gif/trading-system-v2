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
    def test_json_contract_has_empty_series_statuses(self):
        source = (ROOT / "scripts" / "dashboard" / "serve_multi_ma_dashboard.py").read_text(encoding="utf-8")
        self.assertIn('"programStatus": "NORMAL" if program_rows else "DATA_MISSING"', source)
        self.assertIn('"executionStrengthStatus": "NORMAL" if execution_rows else "DATA_MISSING"', source)

    def test_client_normalizes_missing_optional_arrays_before_length(self):
        html = (ROOT / "reports" / "multi-ma" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Array.isArray(data[k])", html)
        self.assertIn("programMinuteSeries.length", html)
        self.assertIn("executionStrengthSeries.length", html)

    def test_client_renders_optional_series_contract_when_fields_are_missing(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        html = ROOT / "reports" / "multi-ma" / "index.html"
        # Execute the real normalizer in the script's lexical scope.  This
        # protects the pre-market/API-error case where either optional array
        # or its status field is absent from latest.json.
        check = r"""
const fs=require('fs'); const h=fs.readFileSync(process.argv[1], 'utf8');
let s=[...h.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
s=s.replace('init();poll();', `
  for (const payload of [{}, {programMinuteSeries:[]},
    {executionStrengthSeries:[]}, {programMinuteSeries:[],executionStrengthSeries:[]},
    {programMinuteSeries:null,executionStrengthSeries:null,programStatus:null,executionStrengthStatus:null}]) {
    normalize(payload);
    if (!Array.isArray(data.programMinuteSeries) || !Array.isArray(data.executionStrengthSeries)) throw new Error('normalization failed');
    if (!data.programStatus || !data.executionStrengthStatus) throw new Error('status default failed');
  }`);
new Function(s)();
"""
        subprocess.run([node, "-e", check, str(html)], check=True)
