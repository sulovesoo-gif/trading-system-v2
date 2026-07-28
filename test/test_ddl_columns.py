from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[1]


class RawDdlTest(unittest.TestCase):
    def test_raw_ddl_has_payload_kst_and_hypertable(self):
        files = sorted((ROOT / "database" / "ddl").glob("1[0-7]_raw_*.sql"))
        self.assertEqual(len(files), 8)
        for file in files:
            content = file.read_text(encoding="utf-8")
            self.assertIn("SET TIME ZONE 'Asia/Seoul';", content, file.name)
            self.assertRegex(content, r"raw_payload\s+JSONB\s+NOT NULL", file.name)
            self.assertIn("create_hypertable", content, file.name)

    def test_legacy_raw_tables_removed(self):
        ddl = ROOT / "database" / "ddl"
        self.assertFalse((ddl / "11_raw_market_flow.sql").exists())
        self.assertFalse((ddl / "12_raw_price.sql").exists())
        self.assertFalse((ddl / "13_raw_futures.sql").exists())
