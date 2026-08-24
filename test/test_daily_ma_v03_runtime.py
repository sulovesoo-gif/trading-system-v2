import unittest
from datetime import datetime

from src.daily_ma_v03.evaluator import DailyMaStrategy
from src.daily_ma_v03.runtime import DailyMaPaperRuntime
from src.daily_ma_v03.contracts import ExecutionBar


class Repository:
    def __init__(self, strategy): self.strategy, self.entries, self.exit_calls = strategy, {}, 0
    def canonical_strategies(self): return [self.strategy]
    def entry_event_exists(self, strategy_id, event_key, snapshot_digest):
        current = self.entries.get((strategy_id, event_key))
        if current is not None and current != snapshot_digest: raise RuntimeError("snapshot mismatch must block")
        return current is not None
    def record_entry(self, **kwargs):
        self.entries[(kwargs["strategy"].strategy_id, kwargs["event"].key())] = kwargs["snapshot_digest"]
        return True
    def evaluate_open_normal_exits(self, *, signal_time): self.exit_calls += 1; return 0


class Raw:
    def source_bar(self, stock_code, at): return {"bar_time": at.isoformat(), "open": 10, "high": 20, "low": 10, "close": 20, "volume": 1}
    def prior_daily_closes(self, stock_code, before, limit): return [10] * limit
    def execution_bars(self, stock_code, at): return (ExecutionBar(datetime(2026, 8, 24, 15, 19), 100),)


class DailyMaPaperRuntimeTest(unittest.TestCase):
    def test_no_write_duplicate_and_normal_exit_are_independent(self):
        strategy = DailyMaStrategy("S1", "005930", "0193W0", "LONG", 3, 5, 3, 5, None, True)
        repo = Repository(strategy)
        runtime = DailyMaPaperRuntime(repository=repo, raw_provider=Raw())
        result = runtime.evaluate_1518(datetime(2026, 8, 24, 15, 18))
        self.assertEqual(1, len(result)); self.assertTrue(result[0].entry_created)
        rerun = runtime.evaluate_1518(datetime(2026, 8, 24, 15, 18))
        self.assertFalse(rerun[0].entry_created)
        self.assertEqual(2, repo.exit_calls)


if __name__ == "__main__": unittest.main()
