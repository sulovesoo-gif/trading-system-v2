import unittest
from pathlib import Path


class DailyMaV03LifecycleFixtureTest(unittest.TestCase):
    def test_fixture_is_explicit_test_only_and_has_no_broker_path(self):
        content = Path("scripts/db/run_daily_ma_v03_paper_lifecycle_fixture.py").read_text(encoding="utf-8")
        self.assertIn("RUN_DAILY_MA_V03_PAPER_LIFECYCLE_FIXTURE", content)
        self.assertIn('settings.name != "trading_system_v2_test"', content)
        self.assertIn("PostgresPaperRuntimeRepository(pool, write_enabled=True)", content)
        self.assertNotIn("KISOrderPostTransport", content)
        self.assertNotIn("KisBrokerAdapter", content)


if __name__ == "__main__":
    unittest.main()
