from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from src.service.sql_analysis_runner_service import StreamingXlsxWriter


ROOT = Path(__file__).resolve().parents[1]


class SqlAnalysisXlsxTest(unittest.TestCase):
    def test_multiple_typed_result_sets_form_valid_xlsx_package(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "result.xlsx"
            writer = StreamingXlsxWriter(target)
            self.assertEqual(2, writer.add_result("RESULT_01", ["id", "amount", "day", "at", "ok", "note"], [
                (1, Decimal("12.50"), date(2026, 8, 28), datetime(2026, 8, 28, 9, 1), True, "한글"),
                (2, None, None, None, False, ""),
            ]))
            self.assertEqual(1, writer.add_result("RESULT_02", ["payload"], [({"a": 1},)]))
            writer.close()
            self.assertTrue(target.is_file())
            with zipfile.ZipFile(target) as package:
                self.assertEqual("RESULT_01", _sheet_names(package)[0])
                self.assertEqual(2, len(_sheet_names(package)))
                self.assertIn("한글", package.read("xl/worksheets/sheet1.xml").decode("utf-8"))


class SqlAnalysisContractTest(unittest.TestCase):
    def test_migration_and_dashboard_are_fail_closed(self):
        migration = (ROOT / "database/migrations/20260828_sql_analysis_runner_additive.sql").read_text(encoding="utf-8")
        server = (ROOT / "scripts/dashboard/serve_multi_ma_dashboard.py").read_text(encoding="utf-8")
        provision = (ROOT / "scripts/admin/provision_sql_analysis_role.py").read_text(encoding="utf-8")
        page = (ROOT / "reports/multi-ma/sql-analysis.html").read_text(encoding="utf-8")
        self.assertIn("sql_analysis_execution_history", migration)
        self.assertIn("hmac.compare_digest", server)
        self.assertIn("/sql-analysis/api/run", server)
        self.assertNotIn("MINUTE_MA_ACTUAL_SEND", server)
        self.assertIn("NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT", provision)
        self.assertIn("RESET default_transaction_read_only", provision)
        self.assertIn("REVOKE CREATE ON SCHEMA public", provision)
        self.assertIn("TEMP 세션 종료", page)
        self.assertIn("Excel 다운로드", page)
        self.assertIn("sqlAnalysisUserCurrentV2", page)
        self.assertNotIn("crypto.randomUUID", page)
        self.assertIn("submitInFlight", page)


def _sheet_names(package: zipfile.ZipFile) -> list[str]:
    from xml.etree import ElementTree
    root = ElementTree.fromstring(package.read("xl/workbook.xml"))
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    return [node.attrib["name"] for node in root.findall("x:sheets/x:sheet", ns)]


if __name__ == "__main__":
    unittest.main()
