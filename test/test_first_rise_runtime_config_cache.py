import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from src.first_rise_breakout.config import FirstRiseRuntimeConfig
from src.first_rise_breakout.models import CandidateState, ResearchState, MinuteBar
from src.first_rise_breakout.runtime import FirstRiseBreakoutRuntime
from src.first_rise_breakout.strategy import FirstRiseBreakoutStrategy
from src.first_rise_breakout.minute_source import SameDayMinutePeakSource
from src.first_rise_breakout.condition_search import ConditionCandidate


DAY = datetime(2026, 9, 30, 9)
CONFIG = FirstRiseRuntimeConfig.from_row(("Y", "09:01", "15:00", "09:01", "10:00", "10000000", "10000000", "100000000"))


def candidate(code="123456", opened=False):
    return CandidateState(uuid4(), DAY.date(), code,
        ResearchState.PAPER_ENTERED if opened else ResearchState.DISCOVERED,
        raw_entry_price=Decimal("100") if opened else None,
        entry_signal_time=DAY + timedelta(minutes=14) if opened else None)


class Repo:
    def __init__(self): self.applied = []; self.hits = []; self.created = []
    def runtime_config(self): return CONFIG
    def previous_regular_close(self, **kwargs): raise AssertionError("OPEN must not query previous_close")
    def apply(self, decision, observation, **kwargs):
        self.applied.append(decision)
        return decision.after
    def record_condition_hits(self, **kwargs): self.hits.extend(kwargs["candidates"])
    def record_candidate(self, **kwargs):
        self.created.append(kwargs)
        raise AssertionError("excluded candidate")


class Source:
    def previous_close(self, **kwargs): return Decimal('95')
    def __init__(self): self.codes = []; self.discarded = []
    def completed_bars_from_open(self, *, stock_code, **kwargs):
        self.codes.append(stock_code)
        return [MinuteBar(DAY + timedelta(minutes=15), Decimal("99"), Decimal("100"), Decimal("98"), Decimal("99"))]
    def discard(self, **kwargs): self.discarded.append(kwargs)


def runtime(repo=None, source=None):
    return FirstRiseBreakoutRuntime(repository=repo or Repo(), strategy=FirstRiseBreakoutStrategy(),
        condition_search=object(), minute_source=source or Source())


class ConfigRuntimeTests(unittest.TestCase):
    def test_durable_daily_loader_used_once_in_cycle(self):
        class DailyRepo(Repo):
            calls=0
            def runtime_config(self): raise AssertionError('must use daily snapshot')
            def runtime_config_for_day(self, *, at):
                self.calls+=1
                return CONFIG
        repo=DailyRepo()
        r=FirstRiseBreakoutRuntime(repository=repo,strategy=FirstRiseBreakoutStrategy(),
            condition_search=object(),minute_source=Source(),now_provider=lambda:DAY)
        r._load_daily_config(at=DAY+timedelta(hours=3))
        self.assertEqual(repo.calls,1)
        r._load_daily_config(at=DAY+timedelta(days=1))
        self.assertEqual(repo.calls,2)

    def test_config_load_once_per_day_and_next_day_failure_does_not_retry(self):
        class DailyRepo(Repo):
            calls = 0
            fail = False
            def runtime_config(self):
                self.calls += 1
                if self.fail:
                    raise ValueError("inactive")
                return CONFIG
        repo = DailyRepo()
        r = FirstRiseBreakoutRuntime(repository=repo, strategy=FirstRiseBreakoutStrategy(),
            condition_search=object(), minute_source=Source(),
            now_provider=lambda: DAY)
        for minute in (1, 2, 300):
            r._load_daily_config(at=DAY+timedelta(minutes=minute))
        self.assertEqual(repo.calls, 1)
        repo.fail = True
        with self.assertLogs("src.first_rise_breakout.runtime", "ERROR"):
            r._load_daily_config(at=DAY+timedelta(days=1))
        r._load_daily_config(at=DAY+timedelta(days=1, minutes=10))
        self.assertEqual(repo.calls, 2)
        self.assertIsNone(r.config)
        r._states = {"123456": candidate(opened=True)}
        r.refresh_completed_bars(at=DAY+timedelta(days=1, minutes=20))
        self.assertEqual(repo.applied[-1].reason, "STOP_ENTRY_BREAK")
        repo.fail = False
        r._load_daily_config(at=DAY+timedelta(days=2))
        self.assertEqual(repo.calls, 3)
        self.assertIs(r.config, CONFIG)

    def test_invalid_config_blocks_entry_but_open_exit_continues(self):
        class Broken(Repo):
            def runtime_config(self): raise ValueError("missing config")
        with self.assertLogs("src.first_rise_breakout.runtime", "ERROR"):
            r = runtime(Broken())
        r._states = {"123456": candidate(opened=True)}
        self.assertEqual(r.scan_once(at=DAY), 0)
        r.refresh_completed_bars(at=DAY + timedelta(hours=6))
        self.assertEqual(r.repository.applied[-1].reason, "STOP_ENTRY_BREAK")

    def test_config_validation(self):
        for row in (None, ("N", "09:01", "15:00", "09:01", "10:00"),
                    ("Y", "bad", "15:00", "09:01", "10:00")):
            with self.subTest(row=row), self.assertRaises(ValueError):
                FirstRiseRuntimeConfig.from_row(row)

    def test_entry_boundaries_and_evidence(self):
        r = runtime()
        self.assertEqual(r.SEARCH_END, CONFIG.paper_entry_cutoff)
        self.assertEqual(r.config.live_entry_cutoff.hour, 10)
        for minute, allowed in ((59, True), (60, False)):
            at = DAY.replace(hour=14) + timedelta(minutes=minute)
            s = candidate().evolve(peak_price=Decimal("100"), peak_time=at-timedelta(minutes=10),
                pullback_low_price=Decimal("98"), pullback_pct=Decimal("0.02"))
            bar = MinuteBar(at, Decimal("99"), Decimal("101"), Decimal("99"), Decimal("101"))
            d = r.strategy.observe_bar(s, bar, previous_close=Decimal("95"), allow_entry=True)
            self.assertEqual(d.create_entry, allowed)
            if allowed:
                r._persist_decision(d, bar)
                self.assertEqual(r.repository.applied[-1].evidence["PAPER_ENTRY_CUTOFF"], "15:00:00")
                self.assertEqual(r.repository.applied[-1].evidence["baseline_strategy_version"], r.strategy.STRATEGY_VERSION)

    def test_cutoff_expires_only_unentered_and_keeps_open_watch_cache(self):
        r = runtime()
        r._states = {"123456": candidate(), "005930": candidate("005930", True)}
        r.expire_once(at=DAY.replace(hour=15))
        self.assertEqual(r._states["123456"].state, ResearchState.EXPIRED)
        self.assertEqual(r._states["005930"].state, ResearchState.PAPER_ENTERED)
        self.assertNotIn("005930", [v["stock_code"] for v in r.minute_source.discarded])

    def test_excluded_hits_audited_without_candidate_or_rest(self):
        r = runtime()
        class Search:
            def resolve_seq(self, name): return "dynamic"
            def candidates(self, seq): return [ConditionCandidate(c, c, {}, 1) for c in ("005930", "000660")]
        r.condition_search = Search()
        r.scan_once(at=DAY.replace(hour=14))
        self.assertEqual(len(r.repository.hits), 2)
        self.assertEqual(r.repository.created, [])
        self.assertEqual(r.minute_source.codes, [])
        self.assertFalse(hasattr(r, "subscriptions"))

    def test_excluded_open_exit_without_previous_close_after_cutoff(self):
        r = runtime()
        r._states = {c: candidate(c, True) for c in ("005930", "000660")}
        r.refresh_completed_bars(at=DAY.replace(hour=15, minute=1))
        self.assertEqual([d.reason for d in r.repository.applied], ["STOP_ENTRY_BREAK"] * 2)

    def test_open_priority_and_excluded_existing_unentered(self):
        r = runtime()
        r.repository.previous_regular_close = lambda **kw: Decimal("95")
        r._states = {"111111": candidate("111111"), "000660": candidate("000660"), "222222": candidate("222222", True)}
        r.refresh_completed_bars(at=DAY.replace(hour=14))
        self.assertEqual(r.minute_source.codes, ["222222", "111111"])

    def test_last_1459_completed_bar_can_enter_on_1500_refresh(self):
        r = runtime()
        at = DAY.replace(hour=14, minute=59)
        s = candidate().evolve(peak_price=Decimal("10000"), peak_time=at-timedelta(minutes=10),
            pullback_low_price=Decimal("9800"), pullback_pct=Decimal("0.02"))
        r._states = {s.stock_code: s}
        r.minute_source.previous_close = lambda **kw: Decimal("9500")
        r.minute_source.completed_bars_from_open = lambda **kw: [MinuteBar(
            at, Decimal("9900"), Decimal("10100"), Decimal("9900"), Decimal("10100"))]
        r.refresh_completed_bars(at=at+timedelta(minutes=1))
        r.expire_once(at=at+timedelta(minutes=1))
        self.assertEqual(r._states[s.stock_code].state, ResearchState.PAPER_ENTERED)
        self.assertTrue(r.repository.applied[0].create_entry)

    def test_refresh_summary_counts_actual_paging(self):
        source = SameDayMinutePeakSource(PagingCollector())
        r = runtime(source=source)
        r._states = {"123456": candidate(opened=True)}
        with self.assertLogs("src.first_rise_breakout.runtime", "INFO") as logs:
            r.refresh_completed_bars(at=DAY+timedelta(minutes=90))
        text = "\n".join(logs.output)
        self.assertIn("fhk_calls=4", text)
        self.assertIn("bootstrap=1", text)
        self.assertIn("rest_errors=0", text)


class PagingCollector:
    def __init__(self): self.calls = 0; self.fail = False
    def collect(self, *, input_hour, **kwargs):
        self.calls += 1
        if self.fail: raise RuntimeError("REST failure")
        cursor = datetime.combine(DAY.date(), datetime.strptime(input_hour, "%H%M%S").time())
        cursor = cursor.replace(second=0)
        return [{"bar_time": cursor-timedelta(minutes=i), "open_price": 100, "high_price": 101,
                 "low_price": 99, "close_price": 100, "volume": 1} for i in range(30)
                if cursor-timedelta(minutes=i) >= DAY]


class MinuteCacheTests(unittest.TestCase):
    def test_bootstrap_incremental_and_gap_overlap(self):
        c = PagingCollector(); s = SameDayMinutePeakSource(c)
        first = s.completed_bars_from_open(stock_code="A", as_of=DAY+timedelta(minutes=90))
        self.assertGreater(c.calls, 1)
        count = c.calls
        second = s.completed_bars_from_open(stock_code="A", as_of=DAY+timedelta(minutes=91))
        self.assertEqual(c.calls-count, 1)
        count = c.calls
        third = s.completed_bars_from_open(stock_code="A", as_of=DAY+timedelta(minutes=155))
        self.assertEqual(c.calls-count, 3)
        self.assertEqual(len(third), 155)
        self.assertEqual(len({b.bar_time for b in third}), len(third))
        self.assertLess(third[-1].bar_time, DAY+timedelta(minutes=155))
        self.assertEqual(s.mode_counts, {"bootstrap": 1, "catch_up": 1, "incremental": 1})
        self.assertEqual(len(first), 90); self.assertEqual(len(second), 91)

    def test_failure_preserves_cache_and_day_changes_do_not_reuse(self):
        c = PagingCollector(); s = SameDayMinutePeakSource(c)
        s.completed_bars_from_open(stock_code="A", as_of=DAY+timedelta(minutes=10))
        original = dict(s._cache[(DAY.date(), "A")])
        c.fail = True
        with self.assertRaises(RuntimeError):
            s.completed_bars_from_open(stock_code="A", as_of=DAY+timedelta(minutes=20))
        self.assertEqual(s._cache[(DAY.date(), "A")], original)
        with self.assertRaises(RuntimeError):
            s.completed_bars_from_open(stock_code="A", as_of=DAY+timedelta(days=1, minutes=20))
        self.assertNotIn((DAY.date(), "A"), s._cache)


if __name__ == "__main__": unittest.main()
