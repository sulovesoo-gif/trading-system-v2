import unittest
from pathlib import Path


class DailyMaV03LiveNoSendSchemaApplyTest(unittest.TestCase):
    def test_apply_scripts_are_explicit_and_test_only(self):
        broker = Path("scripts/db/apply_live_broker_contract.py").read_text(encoding="utf-8")
        live = Path("scripts/db/apply_daily_ma_v03_live_nosend_schema.py").read_text(encoding="utf-8")
        self.assertIn("APPLY_LIVE_BROKER_CONTRACT", broker)
        self.assertIn("APPLY_DAILY_MA_V03_LIVE_NOSEND_SCHEMA", live)
        self.assertIn('settings.name != "trading_system_v2_test"', broker)
        self.assertIn('settings.name != "trading_system_v2_test"', live)
        self.assertNotIn("KISOrderPostTransport", broker + live)


if __name__ == "__main__":
    unittest.main()
