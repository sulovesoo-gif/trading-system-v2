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
from src.first_rise_breakout.models import CandidateState, Observation, ResearchState
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
        self.assertEqual([row.stock_code for row in search.candidates("37")], ["123456", "654321"])
        self.assertEqual(client.calls[0]["params"], {"user_id": "tester"})
        self.assertEqual(client.calls[1]["params"], {"user_id": "tester", "seq": "37"})

    def test_missing_or_duplicate_name_is_fail_closed(self):
        with self.assertRaises(SavedConditionError):
            SavedConditionSearch(FakeClient([{"output2": []}]), user_id="u").resolve_seq("missing")

    def test_kis_documented_zero_result_code_is_an_empty_candidate_list(self):
        class EmptyResultClient:
            last_payload = {"rt_cd": "1", "msg_cd": "MCA05918", "msg1": "종목코드 오류입니다."}

            def get(self, **kwargs):
                raise KISClientError("KIS 업무 오류: MCA05918 종목코드 오류입니다.")

        search = SavedConditionSearch(EmptyResultClient(), user_id="tester")
        self.assertEqual(search.candidates("7"), [])


class StrategyTest(unittest.TestCase):
    def setUp(self):
        self.strategy = FirstRiseBreakoutStrategy()

    def test_pullback_and_same_peak_rebreak_after_nine_minutes_enters(self):
        seeded = self.strategy.seed_peak(state(), peak_price=Decimal("100"), peak_time=AT).after
        pulled = self.strategy.observe(seeded, Observation(AT + timedelta(minutes=2), Decimal("99"))).after
        waiting = self.strategy.observe(pulled, Observation(AT + timedelta(minutes=3), Decimal("98.5"))).after
        decision = self.strategy.observe(waiting, Observation(AT + timedelta(minutes=9), Decimal("100.1")))
        self.assertEqual(pulled.state, ResearchState.PULLBACK)
        self.assertEqual(waiting.state, ResearchState.WAIT_REBREAK)
        self.assertEqual(decision.after.state, ResearchState.PAPER_ENTERED)
        self.assertTrue(decision.create_entry)

    def test_early_rebreak_becomes_new_peak_and_restarts_structure(self):
        seeded = self.strategy.seed_peak(state(), peak_price=Decimal("100"), peak_time=AT).after
        pulled = self.strategy.observe(seeded, Observation(AT + timedelta(minutes=1), Decimal("99"))).after
        decision = self.strategy.observe(pulled, Observation(AT + timedelta(minutes=2), Decimal("101")))
        self.assertEqual(decision.after.state, ResearchState.TRACKING)
        self.assertEqual(decision.after.peak_price, Decimal("101"))
        self.assertFalse(decision.create_entry)

    def test_over_four_percent_rejects_unentered_structure(self):
        seeded = self.strategy.seed_peak(state(), peak_price=Decimal("100"), peak_time=AT).after
        decision = self.strategy.observe(seeded, Observation(AT + timedelta(minutes=2), Decimal("95.9")))
        self.assertEqual(decision.after.state, ResearchState.REJECTED)

    def test_after_ten_without_entry_expires(self):
        seeded = self.strategy.seed_peak(state(), peak_price=Decimal("100"), peak_time=AT).after
        decision = self.strategy.observe(seeded, Observation(datetime(2026, 9, 22, 10, 0, 1), Decimal("99")))
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
        registry.add("123456")
        registry.add("000660")
        collector = FlowRawCollector(
            FakeRawRepository(), ws_url="ws://unused", approval_provider=lambda: "unused",
            dynamic_execution_registry=registry,
        )
        dynamic = [row for row in collector.subscriptions if row == {"tr_id": TR_EXECUTION, "tr_key": "123456"}]
        base = [row for row in collector.subscriptions if row == {"tr_id": TR_EXECUTION, "tr_key": "000660"}]
        self.assertEqual(len(dynamic), 1)
        self.assertEqual(len(base), 1)

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
    def test_default_runtime_clock_is_naive_kst(self):
        runtime = FirstRiseBreakoutRuntime(
            repository=object(), strategy=FirstRiseBreakoutStrategy(), condition_search=object(),
            minute_source=object(), subscriptions=DynamicExecutionRegistry(),
        )
        actual = runtime.now()
        expected = datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
        self.assertIsNone(actual.tzinfo)
        self.assertLess(abs((actual - expected).total_seconds()), 2)

    def test_candidate_transition_entry_and_exit_paths_are_independent(self):
        class Repo:
            def __init__(self):
                self.state = state()
                self.transitions = []
                self.entries = 0
                self.exits = 0
            def active_states(self, **kwargs): return []
            def record_candidate(self, **kwargs): return self.state, True
            def apply(self, decision, observation, **kwargs):
                self.state = decision.after
                self.transitions.append((decision.after.state, decision.reason))
                self.entries += int(decision.create_entry)
                self.exits += int(decision.create_exit)
                return self.state
        class Search:
            def resolve_seq(self, name): return "37"
            def candidates(self, seq): return [ConditionCandidate("123456", "A", {"code": "123456"})]
        class Peak:
            def peak(self, **kwargs): return Decimal("100"), AT, Decimal("100")
        repo, registry = Repo(), DynamicExecutionRegistry()
        runtime = FirstRiseBreakoutRuntime(
            repository=repo, strategy=FirstRiseBreakoutStrategy(), condition_search=Search(),
            minute_source=Peak(), subscriptions=registry,
        )
        runtime.scan_once(at=AT)
        runtime.observe("123456", observed_at=AT + timedelta(minutes=1), price=Decimal("99"))
        runtime.observe("123456", observed_at=AT + timedelta(minutes=2), price=Decimal("98.5"))
        runtime.observe("123456", observed_at=AT + timedelta(minutes=9), price=Decimal("100.1"))
        self.assertEqual(repo.entries, 1)
        self.assertIn("123456", registry.symbols())
        self.assertTrue(runtime.record_exit(
            "123456", observed_at=AT + timedelta(minutes=10), price=Decimal("101"), reason="RESEARCH_EXIT"
        ))
        self.assertEqual(repo.exits, 1)
        self.assertEqual(repo.state.state, ResearchState.PAPER_EXITED)
        self.assertNotIn("123456", registry.symbols())


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


if __name__ == "__main__":
    unittest.main()
