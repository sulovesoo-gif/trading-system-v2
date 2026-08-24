import unittest
from pathlib import Path


class DailyMaV03SchemaApplyTest(unittest.TestCase):
    def test_apply_script_is_explicit_and_test_only(self):
        content = Path("scripts/db/apply_daily_ma_v03_paper_runtime_schema.py").read_text(encoding="utf-8")
        self.assertIn("APPLY_DAILY_MA_V03_PAPER_RUNTIME_SCHEMA", content)
        self.assertIn('settings.name != "trading_system_v2_test"', content)
        self.assertNotIn("KISOrderPostTransport", content)


if __name__ == "__main__":
    unittest.main()
