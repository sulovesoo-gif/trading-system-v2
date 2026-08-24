import unittest
from pathlib import Path


class DailyMaV03SchemaVerifyTest(unittest.TestCase):
    def test_verify_script_is_read_only(self):
        content = Path("scripts/db/verify_daily_ma_v03_paper_runtime_schema.py").read_text(encoding="utf-8")
        self.assertIn("SELECT", content)
        self.assertNotIn("UPDATE", content)
        self.assertNotIn("INSERT", content)
        self.assertNotIn("KISOrderPostTransport", content)


if __name__ == "__main__":
    unittest.main()
