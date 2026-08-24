import unittest
from datetime import datetime

from src.daily_ma_v03.evaluator import DailyMaStrategy
from src.daily_ma_v03.runtime import DailyMaPaperRuntime, OpenDay20Trade, OpenNormalTrade
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
    def open_normal_tracking_trades(self): self.exit_calls += 1; return []
    def record_normal_exit(self, **kwargs): return False


class Raw:
    def source_bar(self, stock_code, at): return {"bar_time": at.isoformat(), "open": 10, "high": 20, "low": 10, "close": 20, "volume": 1}
    def prior_daily_closes(self, stock_code, before, limit): return [10] * limit
    def execution_bars(self, stock_code, at): return (ExecutionBar(datetime(2026, 8, 24, 15, 19), 100),)


class Day20Repository(Repository):
    def open_day20_trades(self): return [OpenDay20Trade(4, datetime(2026, 8, 24, 9, 59), self.strategy)]
    def record_day20_exit(self, **kwargs): self.day20 = kwargs; return True


class Day20Raw(Raw):
    def completed_source_bar(self, stock_code, at): return {"bar_time": at.isoformat(), "close": 80}
    def previous_official_close(self, stock_code, at): return 100
    def execution_bars_after(self, stock_code, at): return (ExecutionBar(datetime(2026, 8, 24, 10, 1), 99),)


class NormalExitRepository(Repository):
    def __init__(self, strategy): super().__init__(strategy); self.normal = None
    def open_normal_tracking_trades(self):
        return [OpenNormalTrade(9, datetime(2026, 8, 23).date(), self.strategy)]
    def record_normal_exit(self, **kwargs): self.normal = kwargs; return True


class NormalExitRaw(Raw):
    def source_bar(self, stock_code, at): return {"bar_time": at.isoformat(), "open": 20, "high": 20, "low": 10, "close": 10, "volume": 1}
    def prior_daily_closes(self, stock_code, before, limit): return [20] * limit


class NoExecutionRaw(Raw):
    def execution_bars(self, stock_code, at): return ()


class NoExecutionRepository(Repository):
    def record_entry(self, **kwargs):
        self.entries[(kwargs["strategy"].strategy_id, kwargs["event"].key())] = kwargs["snapshot_digest"]
        self.no_execution = (kwargs["execution_time"], kwargs["execution_price"])
        return True


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

    def test_day20_uses_next_actual_intraday_execution_bar(self):
        strategy = DailyMaStrategy("S1", "005930", "0193W0", "LONG", 3, 5, 3, 5, None, True)
        repo = Day20Repository(strategy)
        runtime = DailyMaPaperRuntime(repository=repo, raw_provider=Day20Raw())
        self.assertEqual(1, runtime.evaluate_day20(datetime(2026, 8, 24, 10, 0)))
        self.assertEqual(datetime(2026, 8, 24, 10, 1), repo.day20["execution_time"])

    def test_day20_ignores_minutes_before_a_trade_entry(self):
        strategy = DailyMaStrategy("S1", "005930", "0193W0", "LONG", 3, 5, 3, 5, None, True)
        repo = Day20Repository(strategy)
        repo.open_day20_trades = lambda: [OpenDay20Trade(4, datetime(2026, 8, 24, 10, 1), strategy)]
        runtime = DailyMaPaperRuntime(repository=repo, raw_provider=Day20Raw())
        self.assertEqual(0, runtime.evaluate_day20(datetime(2026, 8, 24, 10, 0)))

    def test_strategy_filter_limits_a_write_fixture_to_one_strategy(self):
        strategy = DailyMaStrategy("S1", "005930", "0193W0", "LONG", 3, 5, 3, 5, None, True)
        repo = Repository(strategy)
        runtime = DailyMaPaperRuntime(repository=repo, raw_provider=Raw(), strategy_ids={"OTHER"})
        self.assertEqual((), runtime.evaluate_1518(datetime(2026, 8, 24, 15, 18)))

    def test_existing_open_trade_gets_its_own_normal_exit_evaluation(self):
        # Existing long position's 3/5 normal exit crosses down on today's 15:18
        # completed close. This is independent from whether a new entry exists.
        strategy = DailyMaStrategy("S1", "005930", "0193W0", "LONG", 3, 5, 3, 5, None, True)
        repo = NormalExitRepository(strategy)
        runtime = DailyMaPaperRuntime(repository=repo, raw_provider=NormalExitRaw())
        runtime.evaluate_1518(datetime(2026, 8, 24, 15, 18))
        self.assertEqual(9, repo.normal["paper_trade_id"])
        self.assertEqual(datetime(2026, 8, 24, 15, 19), repo.normal["execution_time"])

    def test_no_execution_bar_records_event_without_inventing_price_or_time(self):
        strategy = DailyMaStrategy("S1", "005930", "0193W0", "LONG", 3, 5, 3, 5, None, True)
        repo = NoExecutionRepository(strategy)
        runtime = DailyMaPaperRuntime(repository=repo, raw_provider=NoExecutionRaw())
        result = runtime.evaluate_1518(datetime(2026, 8, 24, 15, 18))
        self.assertTrue(result[0].no_execution_bar)
        self.assertEqual((None, None), repo.no_execution)
        self.assertFalse(runtime.evaluate_1518(datetime(2026, 8, 24, 15, 18))[0].entry_created)


if __name__ == "__main__": unittest.main()
