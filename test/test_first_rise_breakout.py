from __future__ import annotations

import asyncio
import json
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from src.first_rise_breakout.condition_search import SavedConditionError, SavedConditionSearch
from src.first_rise_breakout.minute_source import SameDayMinutePeakSource
from src.first_rise_breakout.models import CandidateState, MinuteBar, Observation, ResearchState
from src.first_rise_breakout.condition_search import ConditionCandidate
from src.first_rise_breakout.runtime import DynamicExecutionRegistry, FirstRiseBreakoutRuntime
from src.first_rise_breakout.strategy import FirstRiseBreakoutStrategy
from src.collector.raw.kis_client import KISClientError
from src.flow_raw.collector import FlowRawCollector
from src.flow_raw.contracts import TR_EXECUTION


AT = datetime(2026, 9, 22, 9, 10)


def state() -> CandidateState:
    return CandidateState(uuid4(), date(2026, 9, 22), "123456", ResearchState.DISCOVERED)


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


class SavedConditionSearchTest(unittest.TestCase):
    def test_resolves_name_without_hardcoded_seq_and_deduplicates_results(self):
        client = FakeClient([
            {"output2": [{"condition_nm": "OTHER", "seq": "1"},
                          {"condition_nm": "TSV2_오전1차상승후돌파_후보_V1", "seq": "37"}]},
            {"output2": [{"code": "123456", "name": "A"},
                          {"code": "123456", "name": "A"},
                          {"code": "654321", "name": "B"}]},
        ])
        search = SavedConditionSearch(client, user_id="tester")
        self.assertEqual(search.resolve_seq("TSV2_오전1차상승후돌파_후보_V1"), "37")
        candidates = search.candidates("37")
        self.assertEqual([row.stock_code for row in candidates], ["123456", "654321"])
        self.assertEqual([row.result_rank for row in candidates], [1, 3])
        self.assertEqual(client.calls[0]["params"], {"user_id": "tester"})
        self.assertEqual(client.calls[1]["params"], {"user_id": "tester", "seq": "37"})

    def test_missing_or_duplicate_name_is_fail_closed(self):
        with self.assertRaises(SavedConditionError):
            SavedConditionSearch(FakeClient([{"output2": []}]), user_id="u").resolve_seq("missing")

    def test_kis_documented_zero_result_code_is_an_empty_candidate_list(self):
        class EmptyResultClient:
            last_payload = {"rt_cd": "1", "msg_cd": "MCA05918", "msg1": "종목코드 오류입니다."}
            last_http_status = 200

            def get(self, **kwargs):
                raise KISClientError("KIS 업무 오류: MCA05918 종목코드 오류입니다.")

        search = SavedConditionSearch(EmptyResultClient(), user_id="tester")
        self.assertEqual(search.candidates("7"), [])
        self.assertEqual(search.last_http_status, 200)
        self.assertEqual(search.last_kis_code, "MCA05918")


class StrategyTest(unittest.TestCase):
    def setUp(self):
        self.strategy = FirstRiseBreakoutStrategy()

    @staticmethod
    def bar(at, *, open_, high, low, close=None):
        return MinuteBar(
            at, Decimal(open_), Decimal(high), Decimal(low), Decimal(close or open_),
        )

    def test_pullback_and_same_peak_rebreak_after_nine_minutes_enters(self):
        seeded = self.strategy.seed_peak(state(), peak_price=Decimal("100"), peak_time=AT).after
        pulled = self.strategy.observe_bar(
            seeded, self.bar(AT + timedelta(minutes=2), open_="99", high="99.5", low="99"),
            previous_close=Decimal("95"),
        ).after
        waiting = self.strategy.observe_bar(
            pulled, self.bar(AT + timedelta(minutes=3), open_="99", high="99.4", low="98.5"),
            previous_close=Decimal("95"),
        ).after
        decision = self.strategy.observe_bar(
            waiting, self.bar(AT + timedelta(minutes=9), open_="99.8", high="100.1", low="99.7"),
            previous_close=Decimal("95"),
        )
        self.assertEqual(pulled.state, ResearchState.PULLBACK)
        self.assertEqual(waiting.state, ResearchState.WAIT_REBREAK)
        self.assertEqual(decision.after.state, ResearchState.PAPER_ENTERED)
        self.assertTrue(decision.create_entry)

    def test_early_rebreak_becomes_new_peak_and_restarts_structure(self):
        seeded = self.strategy.seed_peak(state(), peak_price=Decimal("100"), peak_time=AT).after
        pulled = self.strategy.observe_bar(
            seeded, self.bar(AT + timedelta(minutes=1), open_="99", high="99.5", low="99"),
            previous_close=Decimal("95"),
        ).after
        decision = self.strategy.observe_bar(
            pulled, self.bar(AT + timedelta(minutes=2), open_="100.5", high="101", low="100"),
            previous_close=Decimal("95"),
        )
        self.assertEqual(decision.after.state, ResearchState.TRACKING)
        self.assertEqual(decision.after.peak_price, Decimal("101"))
        self.assertFalse(decision.create_entry)

    def test_over_four_percent_invalidates_only_the_peak_structure(self):
        seeded = self.strategy.seed_peak(state(), peak_price=Decimal("100"), peak_time=AT).after
        decision = self.strategy.observe_bar(
            seeded, self.bar(AT + timedelta(minutes=2), open_="97", high="98", low="95.9"),
            previous_close=Decimal("95"),
        )
        self.assertEqual(decision.after.state, ResearchState.TRACKING)
        self.assertEqual(decision.reason, "PULLBACK_OVER_4_STRUCTURE_INVALIDATED")

    def test_after_ten_without_entry_expires(self):
        seeded = self.strategy.seed_peak(state(), peak_price=Decimal("100"), peak_time=AT).after
        decision = self.strategy.expire(seeded, at=datetime(2026, 9, 22, 10, 0, 1))
        self.assertEqual(decision.after.state, ResearchState.EXPIRED)

    def test_explicit_paper_exit_path_only_closes_entered_candidate(self):
        entered = state().evolve(state=ResearchState.PAPER_ENTERED, entry_event_key="entry")
        decision = self.strategy.exit(entered, Observation(AT, Decimal("102")), reason="RESEARCH_EXIT")
        self.assertTrue(decision.create_exit)
        self.assertEqual(decision.after.state, ResearchState.PAPER_EXITED)


class FakeMinuteCollector:
    def __init__(self):
        self.calls = 0

    def collect(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return [
                {"bar_time": datetime(2026, 9, 22, 9, 9), "high_price": Decimal("101"), "close_price": Decimal("100")},
                {"bar_time": datetime(2026, 9, 22, 9, 10), "high_price": Decimal("100"), "close_price": Decimal("99")},
            ]
        return [
            {"bar_time": datetime(2026, 9, 22, 9, 0), "high_price": Decimal("102"), "close_price": Decimal("101")},
            {"bar_time": datetime(2026, 9, 22, 9, 1), "high_price": Decimal("100"), "close_price": Decimal("100")},
        ]


class MinuteSourceTest(unittest.TestCase):
    def test_walks_back_to_open_and_uses_actual_high(self):
        result = SameDayMinutePeakSource(FakeMinuteCollector()).peak(stock_code="123456", until=AT)
        self.assertEqual(result, (Decimal("102"), datetime(2026, 9, 22, 9, 0), Decimal("99")))


class FakeRawRepository:
    def recent_hashes(self, **kwargs): return set()


class DynamicSubscriptionTest(unittest.TestCase):
    def test_existing_socket_subscription_set_adds_only_dynamic_execution(self):
        registry = DynamicExecutionRegistry()
        registry.add("123456", owner="first_rise_breakout")
        registry.add("000660", owner="first_rise_breakout")
        collector = FlowRawCollector(
            FakeRawRepository(), ws_url="ws://unused", approval_provider=lambda: "unused",
            dynamic_execution_registry=registry,
        )
        dynamic = [row for row in collector.subscriptions if row == {"tr_id": TR_EXECUTION, "tr_key": "123456"}]
        base = [row for row in collector.subscriptions if row == {"tr_id": TR_EXECUTION, "tr_key": "000660"}]
        self.assertEqual(len(dynamic), 1)
        self.assertEqual(len(base), 1)

    def test_first_rise_release_keeps_another_owner_subscription(self):
        registry = DynamicExecutionRegistry()
        registry.add("123456", owner="existing_v2_feature")
        registry.add("123456", owner=FirstRiseBreakoutRuntime.SUBSCRIPTION_OWNER)
        collector = FlowRawCollector(
            FakeRawRepository(), ws_url="ws://unused", approval_provider=lambda: "unused",
            dynamic_execution_registry=registry,
        )

        registry.discard("123456", owner=FirstRiseBreakoutRuntime.SUBSCRIPTION_OWNER)

        self.assertEqual(registry.symbols(), {"123456"})
        self.assertEqual(
            collector.dynamic_subscriptions,
            [{"tr_id": TR_EXECUTION, "tr_key": "123456"}],
        )

    def test_first_rise_release_never_removes_base_subscription(self):
        registry = DynamicExecutionRegistry()
        registry.add("000660", owner=FirstRiseBreakoutRuntime.SUBSCRIPTION_OWNER)
        collector = FlowRawCollector(
            FakeRawRepository(), ws_url="ws://unused", approval_provider=lambda: "unused",
            dynamic_execution_registry=registry,
        )

        registry.discard("000660", owner=FirstRiseBreakoutRuntime.SUBSCRIPTION_OWNER)

        self.assertIn({"tr_id": TR_EXECUTION, "tr_key": "000660"}, collector.subscriptions)
        self.assertEqual(collector.dynamic_subscriptions, [])

    def test_daily_restore_clears_only_first_rise_owner(self):
        class Repo:
            def active_states(self, **kwargs):
                return []

        registry = DynamicExecutionRegistry()
        registry.add("123456", owner="existing_v2_feature")
        registry.add("123456", owner=FirstRiseBreakoutRuntime.SUBSCRIPTION_OWNER)
        registry.add("654321", owner=FirstRiseBreakoutRuntime.SUBSCRIPTION_OWNER)
        runtime = FirstRiseBreakoutRuntime(
            repository=Repo(), strategy=FirstRiseBreakoutStrategy(), condition_search=object(),
            minute_source=object(), subscriptions=registry,
        )

        runtime.restore(at=AT)

        self.assertEqual(registry.symbols(), {"123456"})
        self.assertEqual(registry.symbols(owner=FirstRiseBreakoutRuntime.SUBSCRIPTION_OWNER), set())

    def test_subscription_and_unsubscription_use_existing_socket_protocol(self):
        class Socket:
            def __init__(self): self.sent = []
            async def send(self, payload): self.sent.append(json.loads(payload))
            async def recv(self): return json.dumps({"header": {"tr_id": TR_EXECUTION}, "body": {"rt_cd": "0", "msg1": "OK"}})
        collector = FlowRawCollector(FakeRawRepository(), ws_url="ws://unused", approval_provider=lambda: "unused")
        socket = Socket()
        asyncio.run(collector._change_subscription(
            socket, approval="masked", subscription={"tr_id": TR_EXECUTION, "tr_key": "123456"},
            tr_type="1", deferred_frames=[],
        ))
        asyncio.run(collector._change_subscription(
            socket, approval="masked", subscription={"tr_id": TR_EXECUTION, "tr_key": "123456"},
            tr_type="2", deferred_frames=[],
        ))
        self.assertEqual([item["header"]["tr_type"] for item in socket.sent], ["1", "2"])


class RuntimePersistencePathTest(unittest.TestCase):
    def test_empty_poll_and_end_of_window_summary_are_logged(self):
        class Repo:
            def __init__(self): self.hit_rows = 0
            def active_states(self, **kwargs): return []
            def record_condition_hits(self, **kwargs):
                self.hit_rows += len(kwargs["candidates"])
                return len(kwargs["candidates"])
        class Search:
            last_http_status = 200
            last_kis_code = "MCA05918"
            def resolve_seq(self, name): return "7"
            def candidates(self, seq): return []

        repo = Repo()
        runtime = FirstRiseBreakoutRuntime(
            repository=repo, strategy=FirstRiseBreakoutStrategy(), condition_search=Search(),
            minute_source=object(), subscriptions=DynamicExecutionRegistry(),
        )
        with self.assertLogs("src.first_rise_breakout.runtime", level="INFO") as captured:
            runtime.scan_once(at=AT)
            runtime.expire_once(at=datetime(2026, 9, 22, 10, 0, 1))

        output = "\n".join(captured.output)
        self.assertIn("FIRST_RISE_POLL time=2026-09-22T09:10:00", output)
        self.assertIn("condition=TSV2_오전1차상승후돌파_후보_V1", output)
        self.assertIn("seq=7", output)
        self.assertIn("http_status=200", output)
        self.assertIn("kis_code=MCA05918", output)
        self.assertIn("result_count=0 empty_result=true", output)
        self.assertIn("FIRST_RISE_POLL_SUMMARY date=2026-09-22 poll_count=1 discovered_unique=0 errors=0", output)
        self.assertEqual(repo.hit_rows, 0)

    def test_failed_poll_is_logged_and_counted_once(self):
        class Search:
            last_http_status = 500
            last_kis_code = "KIS_FAILURE"
            def resolve_seq(self, name): return "7"
            def candidates(self, seq): raise KISClientError("failed")

        runtime = FirstRiseBreakoutRuntime(
            repository=object(), strategy=FirstRiseBreakoutStrategy(), condition_search=Search(),
            minute_source=object(), subscriptions=DynamicExecutionRegistry(),
        )
        with self.assertLogs("src.first_rise_breakout.runtime", level="INFO") as captured:
            with self.assertRaises(KISClientError):
                runtime.scan_once(at=AT)
            runtime.expire_once(at=datetime(2026, 9, 22, 10, 0, 1))

        output = "\n".join(captured.output)
        self.assertIn("kis_code=KIS_FAILURE result_count=ERROR", output)
        self.assertIn("poll_count=1 discovered_unique=0 errors=1", output)

    def test_default_runtime_clock_is_naive_kst(self):
        runtime = FirstRiseBreakoutRuntime(
            repository=object(), strategy=FirstRiseBreakoutStrategy(), condition_search=object(),
            minute_source=object(), subscriptions=DynamicExecutionRegistry(),
        )
        actual = runtime.now()
        expected = datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
        self.assertIsNone(actual.tzinfo)
        self.assertLess(abs((actual - expected).total_seconds()), 2)

    def test_each_poll_persists_a_separate_hit_batch(self):
        candidates = [
            ConditionCandidate("111111", "A", {"code": "111111"}, 1),
            ConditionCandidate("222222", "B", {"code": "222222"}, 2),
            ConditionCandidate("333333", "C", {"code": "333333"}, 3),
        ]
        class Repo:
            def __init__(self): self.hits = []
            def record_condition_hits(self, **kwargs):
                self.hits.extend((kwargs["poll_time"], row.stock_code) for row in kwargs["candidates"])
                return len(kwargs["candidates"])
            def record_candidate(self, **kwargs): return state(), False
        class Search:
            def resolve_seq(self, name): return "7"
            def candidates(self, seq): return candidates

        repo = Repo()
        runtime = FirstRiseBreakoutRuntime(
            repository=repo, strategy=FirstRiseBreakoutStrategy(), condition_search=Search(),
            minute_source=object(), subscriptions=DynamicExecutionRegistry(),
        )
        runtime.scan_once(at=AT)
        runtime.scan_once(at=AT + timedelta(minutes=1))

        self.assertEqual(len(repo.hits), 6)
        self.assertEqual(len(set(repo.hits)), 6)

    def test_hit_persistence_precedes_subscription_failure(self):
        candidate = ConditionCandidate("111111", "A", {"code": "111111"}, 1)
        class Repo:
            def __init__(self): self.hits = []
            def record_condition_hits(self, **kwargs):
                self.hits.extend(kwargs["candidates"])
                return len(kwargs["candidates"])
            def record_candidate(self, **kwargs): return state(), False
        class Search:
            def resolve_seq(self, name): return "7"
            def candidates(self, seq): return [candidate]
        class FailingRegistry(DynamicExecutionRegistry):
            def add(self, stock_code, *, owner):
                raise RuntimeError("subscribe unavailable")

        repo = Repo()
        runtime = FirstRiseBreakoutRuntime(
            repository=repo, strategy=FirstRiseBreakoutStrategy(), condition_search=Search(),
            minute_source=object(), subscriptions=FailingRegistry(),
        )
        with self.assertRaises(RuntimeError):
            runtime.scan_once(at=AT)

        self.assertEqual([row.stock_code for row in repo.hits], ["111111"])

    def test_candidate_transition_entry_and_exit_paths_are_independent(self):
        class Repo:
            def __init__(self):
                self.state = state()
                self.transitions = []
                self.entries = 0
                self.exits = 0
            def active_states(self, **kwargs): return []
            def record_condition_hits(self, **kwargs): return len(kwargs["candidates"])
            def record_candidate(self, **kwargs): return self.state, True
            def apply(self, decision, observation, **kwargs):
                self.state = decision.after
                self.transitions.append((decision.after.state, decision.reason))
                self.entries += int(decision.create_entry)
                self.exits += int(decision.create_exit)
                return self.state
        class Search:
            def resolve_seq(self, name): return "37"
            def candidates(self, seq):
                return [ConditionCandidate("123456", "A", {"code": "123456"}, 1)]
        class Source:
            def __init__(self):
                self.bars = [MinuteBar(
                    datetime(2026, 9, 22, 9, 1), Decimal("9900"), Decimal("10000"),
                    Decimal("9900"), Decimal("10000"), 1_000_000, Decimal("1000000000"),
                )]
            def completed_bars_from_open(self, **kwargs): return list(self.bars)
        source = Source()
        def previous_regular_close(**kwargs): return Decimal("9500")
        repo, registry = Repo(), DynamicExecutionRegistry()
        repo.previous_regular_close = previous_regular_close
        runtime = FirstRiseBreakoutRuntime(
            repository=repo, strategy=FirstRiseBreakoutStrategy(), condition_search=Search(),
            minute_source=source, subscriptions=registry,
        )
        with self.assertLogs("src.first_rise_breakout.runtime", level="INFO") as captured:
            runtime.scan_once(at=AT)
        self.assertIn("FIRST_RISE_DISCOVERED stock_code=123456 stock_name=A", "\n".join(captured.output))
        source.bars.extend([
            MinuteBar(datetime(2026, 9, 22, 9, 11), Decimal("9900"), Decimal("9950"), Decimal("9900"), Decimal("9900"), accumulated_amount=Decimal("1000000000")),
            MinuteBar(datetime(2026, 9, 22, 9, 12), Decimal("9900"), Decimal("9940"), Decimal("9850"), Decimal("9850"), accumulated_amount=Decimal("1000000000")),
            MinuteBar(datetime(2026, 9, 22, 9, 20), Decimal("9980"), Decimal("10010"), Decimal("9970"), Decimal("10010"), accumulated_amount=Decimal("1000000000")),
        ])
        runtime.refresh_completed_bars(at=datetime(2026, 9, 22, 9, 21))
        self.assertEqual(repo.entries, 1)
        self.assertIn(
            "123456",
            registry.symbols(owner=FirstRiseBreakoutRuntime.SUBSCRIPTION_OWNER),
        )
        self.assertTrue(runtime.record_exit(
            "123456", observed_at=AT + timedelta(minutes=10), price=Decimal("10100"), reason="RESEARCH_EXIT"
        ))
        self.assertEqual(repo.exits, 1)
        self.assertEqual(repo.state.state, ResearchState.PAPER_EXITED)
        self.assertNotIn(
            "123456",
            registry.symbols(owner=FirstRiseBreakoutRuntime.SUBSCRIPTION_OWNER),
        )


class FrozenResearchContractTest(unittest.TestCase):
    def setUp(self):
        self.strategy = FirstRiseBreakoutStrategy()

    @staticmethod
    def bar(minute, *, open_, high, low, close, hour=9):
        return MinuteBar(
            datetime(2026, 9, 22, hour, minute),
            Decimal(open_), Decimal(high), Decimal(low), Decimal(close),
            volume=1000, accumulated_amount=Decimal("1000000000"),
        )

    def test_bootstrap_replays_every_bar_and_new_record_high_resets_peak(self):
        current = state()
        bars = [
            self.bar(5, open_="99", high="100", low="99", close="100"),
            self.bar(10, open_="99", high="99", low="97", close="98"),
            self.bar(15, open_="100", high="101", low="99", close="101"),
            self.bar(20, open_="100", high="100", low="98", close="99"),
        ]
        for bar in bars:
            current = self.strategy.observe_bar(
                current, bar, previous_close=Decimal("95"),
                allow_entry=False, bootstrap=True,
            ).after
        self.assertEqual(current.peak_time, datetime(2026, 9, 22, 9, 15))
        self.assertEqual(current.peak_price, Decimal("101"))
        self.assertEqual(current.pullback_low_price, Decimal("98"))
        self.assertEqual(current.state, ResearchState.PULLBACK)

    def test_pullback_below_half_percent_does_not_rest(self):
        seeded = self.strategy.seed_peak(
            state(), peak_price=Decimal("100"), peak_time=datetime(2026, 9, 22, 9, 5),
        ).after
        result = self.strategy.observe_bar(
            seeded, self.bar(10, open_="100", high="100", low="99.6", close="99.8"),
            previous_close=Decimal("95"),
        )
        self.assertEqual(result.after.state, ResearchState.TRACKING)

    def test_gap_and_ordinary_breakout_raw_prices(self):
        base = self.strategy.seed_peak(
            state(), peak_price=Decimal("100"), peak_time=datetime(2026, 9, 22, 9, 5),
        ).after
        rested = self.strategy.observe_bar(
            base, self.bar(7, open_="99", high="99.5", low="99", close="99"),
            previous_close=Decimal("95"),
        ).after
        gap = self.strategy.observe_bar(
            rested, self.bar(14, open_="101", high="102", low="100", close="101"),
            previous_close=Decimal("95"),
        )
        ordinary = self.strategy.observe_bar(
            rested, self.bar(14, open_="99.8", high="100.1", low="99.7", close="100.1"),
            previous_close=Decimal("95"),
        )
        self.assertEqual(gap.raw_execution_price, Decimal("101"))
        self.assertEqual(ordinary.raw_execution_price, Decimal("100"))

    def test_strategy_liquidity_gate_is_separate_from_condition_discovery(self):
        base = self.strategy.seed_peak(
            state(), peak_price=Decimal("10000"), peak_time=datetime(2026, 9, 22, 9, 5),
        ).after
        rested = self.strategy.observe_bar(
            base, MinuteBar(
                datetime(2026, 9, 22, 9, 7), Decimal("9900"), Decimal("9950"),
                Decimal("9900"), Decimal("9900"),
            ), previous_close=Decimal("9500"),
        ).after
        breakout = MinuteBar(
            datetime(2026, 9, 22, 9, 14), Decimal("9980"), Decimal("10010"),
            Decimal("9970"), Decimal("10010"),
        )
        not_ready = self.strategy.observe_bar(
            rested, breakout, previous_close=Decimal("9500"),
            session_volume=999_999, session_amount=Decimal("1000000000"),
        )
        ready = self.strategy.observe_bar(
            rested, breakout, previous_close=Decimal("9500"),
            session_volume=1_000_000, session_amount=Decimal("1000000000"),
        )
        self.assertFalse(not_ready.create_entry)
        self.assertEqual(not_ready.reason, "HISTORICAL_LIQUIDITY_NOT_YET_CONFIRMED")
        self.assertTrue(ready.create_entry)

    def test_stop_is_strictly_below_entry_and_uses_gap_or_entry_price(self):
        entered = state().evolve(
            state=ResearchState.PAPER_ENTERED,
            entry_signal_time=datetime(2026, 9, 22, 9, 10),
            raw_entry_price=Decimal("100"),
            entry_execution_price=self.strategy.buy_execution_price(Decimal("100")),
        )
        equal_low = self.bar(11, open_="101", high="102", low="100", close="100")
        broken = self.bar(12, open_="99", high="100", low="98", close="99")
        result = self.strategy.exit_from_completed_bars(entered, [equal_low, broken])
        self.assertEqual(result.reason, "STOP_ENTRY_BREAK")
        self.assertEqual(result.signal_time, broken.bar_time)
        self.assertEqual(result.raw_execution_price, Decimal("99"))

    def test_profit_exit_uses_completed_three_minute_tenkan_then_next_minute_open(self):
        entered = state().evolve(
            state=ResearchState.PAPER_ENTERED,
            entry_signal_time=datetime(2026, 9, 22, 9, 10),
            raw_entry_price=Decimal("100"),
            entry_execution_price=self.strategy.buy_execution_price(Decimal("100")),
        )
        bars = []
        for minute in range(27):
            bars.append(self.bar(
                minute, open_="101", high="102", low="100", close="101.5",
            ))
        bars.extend([
            self.bar(27, open_="103", high="104", low="101", close="102.5"),
            self.bar(28, open_="102.5", high="103", low="100.5", close="101.5"),
            self.bar(29, open_="101.5", high="102", low="100.5", close="101"),
            self.bar(30, open_="100.8", high="101", low="100", close="100.5"),
        ])
        result = self.strategy.exit_from_completed_bars(entered, bars)
        self.assertEqual(result.reason, "BOOK_TENKAN_PROFIT")
        self.assertEqual(result.signal_time, datetime(2026, 9, 22, 9, 30))
        self.assertEqual(result.after.last_observed_at, datetime(2026, 9, 22, 9, 30))
        self.assertEqual(result.raw_execution_price, Decimal("100.8"))

    def test_session_close_uses_last_krx_close(self):
        entered = state().evolve(
            state=ResearchState.PAPER_ENTERED,
            entry_signal_time=datetime(2026, 9, 22, 9, 10),
            raw_entry_price=Decimal("100"),
            entry_execution_price=self.strategy.buy_execution_price(Decimal("100")),
        )
        last = MinuteBar(
            datetime(2026, 9, 22, 15, 30), Decimal("102"), Decimal("103"),
            Decimal("100"), Decimal("102.5"),
        )
        result = self.strategy.exit_from_completed_bars(entered, [last], session_ended=True)
        self.assertEqual(result.reason, "SESSION_CLOSE")
        self.assertEqual(result.raw_execution_price, Decimal("102.5"))

    def test_kmode_one_cost_and_compound_match_first_verification_trade(self):
        entry = self.strategy.entry_plan(
            capital=Decimal("10000000"), raw_entry_price=Decimal("945000"),
        )
        self.assertEqual(entry["quantity"], 10)
        exit_ = self.strategy.exit_plan(
            capital_before=Decimal("10000000"),
            cash_remaining=entry["cash_remaining"], quantity=entry["quantity"],
            raw_exit_price=Decimal("945000"),
        )
        self.assertAlmostEqual(float(exit_["capital_after"]), 9974554.4197, places=4)

    def test_ticks_do_not_create_entry(self):
        seeded = self.strategy.seed_peak(state(), peak_price=Decimal("100"), peak_time=AT).after
        decision = self.strategy.observe(
            seeded, Observation(AT + timedelta(minutes=10), Decimal("101"), "H0STCNT0"),
        )
        self.assertFalse(decision.changed)
        self.assertFalse(decision.create_entry)
        self.assertEqual(decision.reason, "TICK_AUXILIARY_ONLY")


class MigrationScopeTest(unittest.TestCase):
    def test_migration_is_additive_and_does_not_mutate_existing_raw_or_strategy_tables(self):
        sql = (Path(__file__).parents[1] / "database/migrations/20260922_first_rise_breakout_research.sql").read_text(encoding="utf-8")
        self.assertEqual(sql.count("CREATE TABLE IF NOT EXISTS first_rise_breakout_"), 4)
        upper = sql.upper()
        self.assertNotIn("ALTER TABLE RAW_", upper)
        self.assertNotIn("UPDATE RAW_", upper)
        self.assertNotIn("DELETE FROM RAW_", upper)
        self.assertNotIn("LIVE_ENABLED", upper)
        self.assertNotIn("ACTUAL_ENABLED", upper)

    def test_condition_hit_migration_is_additive_and_poll_idempotent(self):
        sql = (Path(__file__).parents[1] / "database/migrations/20260922_first_rise_breakout_condition_hit.sql").read_text(encoding="utf-8")
        upper = sql.upper()
        self.assertIn("CREATE TABLE IF NOT EXISTS FIRST_RISE_BREAKOUT_CONDITION_HIT", upper)
        self.assertIn("UNIQUE (POLL_TIME, CONDITION_NAME, CONDITION_SEQ, STOCK_CODE)", upper)
        self.assertNotIn("ALTER TABLE RAW_", upper)
        self.assertNotIn("UPDATE RAW_", upper)
        self.assertNotIn("DELETE FROM RAW_", upper)


if __name__ == "__main__":
    unittest.main()
