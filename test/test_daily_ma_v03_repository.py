import unittest
from pathlib import Path

from src.daily_ma_v03.repository import PostgresPaperRuntimeRepository


class PaperRuntimeRepositoryTest(unittest.TestCase):
    def test_no_write_mode_cannot_create_paper_entry(self):
        repository = PostgresPaperRuntimeRepository(pool=None, write_enabled=False)
        self.assertFalse(repository.record_entry(strategy=None, event=None, snapshot={}, snapshot_digest="x", execution_time=None, execution_price=None))

    def test_repository_queries_canonical_v03_view(self):
        source = Path("src/daily_ma_v03/repository.py").read_text(encoding="utf-8")
        self.assertIn("vw_daily_strategy_v03_runtime", source)
        self.assertIn("DAILY_MA_V03", source)
        self.assertNotIn("KISOrderPostTransport", source)


if __name__ == "__main__":
    unittest.main()
