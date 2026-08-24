import unittest
from pathlib import Path


class DailyMaV04CapitalSchemaTest(unittest.TestCase):
    def test_additive_schema_has_exactly_once_and_no_retry_guards(self):
        sql = Path("database/migrations/20260824_daily_strategy_ma_v04_capital_additive.sql").read_text(encoding="utf-8")
        self.assertIn("daily_strategy_compound_capital", sql)
        self.assertIn("daily_strategy_live_capital_settlement", sql)
        self.assertIn("live_trade_id BIGINT NOT NULL UNIQUE", sql)
        self.assertIn("daily_strategy_live_entry_skip", sql)
        self.assertIn("retry_allowed = FALSE", sql)
        self.assertIn("capital_settled_at", sql)

    def test_runtime_has_no_transport_dependency(self):
        text = Path("src/daily_ma_v03/capital_repository.py").read_text(encoding="utf-8") + Path("src/daily_ma_v03/capital_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn("post_once", text)
        self.assertNotIn("KISOrderPostTransport", text)


if __name__ == "__main__":
    unittest.main()
