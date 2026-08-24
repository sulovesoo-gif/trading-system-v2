import unittest
from pathlib import Path


class DailyMaV03RunnerTest(unittest.TestCase):
    def test_runner_defaults_to_no_write_and_has_no_broker_dependency(self):
        content = Path("scripts/runtime/run_daily_ma_v03_paper.py").read_text(encoding="utf-8")
        self.assertIn('write_enabled = args.write and os.getenv("DAILY_MA_V03_PAPER_WRITE", "N") == "Y"', content)
        self.assertIn('"order_post": 0', content)
        self.assertIn("--strategy-id", content)
        self.assertIn("--strategy-id is required for limited DB write", content)
        self.assertNotIn("KISOrderPostTransport", content)
        self.assertNotIn("KisBrokerAdapter", content)


if __name__ == "__main__":
    unittest.main()
