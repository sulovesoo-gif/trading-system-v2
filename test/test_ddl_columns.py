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

    def test_venue_is_limited_to_stock_and_futures_raw_tables(self):
        ddl = ROOT / "database" / "ddl"
        expected = {
            "12_raw_stock_quote.sql", "13_raw_stock_execution.sql", "14_raw_stock_minute.sql",
            "15_raw_stock_daily.sql", "16_raw_futures_quote.sql", "17_raw_futures_minute.sql",
        }
        for file in sorted(ddl.glob("1[0-7]_raw_*.sql")):
            content = file.read_text(encoding="utf-8")
            if file.name in expected:
                self.assertIn("trading_venue", content, file.name)
                self.assertIn("'KRX', 'NXT', 'INTEGRATED'", content, file.name)
            else:
                self.assertNotIn("trading_venue", content, file.name)

    def test_backfill_metadata_ddl_exists(self):
        ddl = ROOT / "database" / "ddl"
        self.assertTrue((ddl / "04_backfill_job.sql").exists())
        self.assertTrue((ddl / "05_backfill_segment.sql").exists())
        manifest = (ddl / "06_futures_backfill_manifest.sql").read_text(encoding="utf-8")
        self.assertIn("futures_backfill_manifest", manifest)
        self.assertIn("API_VERIFIED_UNCONFIRMED", manifest)
        self.assertIn("OFFICIAL_MASTER_VERIFIED", manifest)

    def test_first_phase_stock_minute_targets_are_seeded(self):
        seed = (ROOT / "database" / "seed" / "01_stock_minute_backfill_targets.sql").read_text(encoding="utf-8")
        for stock_code in ("000660", "0193T0", "0197X0", "005930", "0193W0", "0193L0"):
            self.assertIn(stock_code, seed)

    def test_stock_minute_snapshot_uses_its_own_raw_table(self):
        content = (ROOT / "database" / "ddl" / "23_raw_stock_minute_snapshot.sql").read_text(encoding="utf-8")
        self.assertIn("raw_stock_minute_snapshot", content)
        self.assertIn("target_bar_time", content)
        self.assertIn("snapshot_second", content)
        self.assertIn("raw_payload                 JSONB        NOT NULL", content)
