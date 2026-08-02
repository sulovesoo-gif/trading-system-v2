import tempfile, unittest
from pathlib import Path
from scripts.analysis.render_multi_ma_performance_report import render_report
class ReportTest(unittest.TestCase):
 def test_static_report_contains_exit_markers(self):
  with tempfile.TemporaryDirectory() as d:
   path=render_report(summaries=[("SIGNAL_1","SEC_05","MA_3_5_10","CLOSE",12,1,1,0)],trades=[],output=Path(d)/"report.html")
   text=path.read_text(encoding="utf-8"); self.assertIn("SESSION_CLOSE",text); self.assertIn("SIGNAL_1",text)
