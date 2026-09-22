from __future__ import annotations

import unittest
from datetime import datetime, time, timedelta
from pathlib import Path

from src.first_rise_breakout.condition_search import ConditionCandidate
from src.orderbook_condition_research.runtime import OrderbookConditionResearchRuntime


AT = datetime(2026, 9, 22, 9, 10)
SELL = "TSV2_호가연구_매도잔량우위_V1"
BUY = "TSV2_호가연구_매수잔량우위_V1"


def candidate(code: str, rank: int) -> ConditionCandidate:
    return ConditionCandidate(
        stock_code=code,
        stock_name=f"NAME-{code}",
        raw_payload={"code": code, "price": "12345", "volume": "67890"},
        result_rank=rank,
    )


class FakeRepository:
    def __init__(self) -> None:
        self.rows = {}

    def record_hits(self, **kwargs) -> int:
        inserted = 0
        for row in kwargs["candidates"]:
            key = (
                kwargs["poll_time"], kwargs["condition_name"],
                kwargs["condition_seq"], row.stock_code,
            )
            if key in self.rows:
                continue
            self.rows[key] = {
                "stock_name": row.stock_name,
                "result_order": row.result_rank,
                "raw_payload": row.raw_payload,
            }
            inserted += 1
        return inserted


class FakeSearch:
    last_http_status = 200
    last_kis_code = "0"

    def __init__(self, outputs) -> None:
        self.outputs = outputs
        self.resolved = []

    def resolve_seq(self, name):
        self.resolved.append(name)
        return {SELL: "11", BUY: "12"}[name]

    def candidates(self, seq):
        return list(self.outputs.get(seq, []))


class OrderbookConditionResearchTest(unittest.TestCase):
    def test_two_conditions_resolve_by_name_and_store_each_poll(self):
        search = FakeSearch({
            "11": [candidate("111111", 1), candidate("222222", 2), candidate("333333", 3)],
            "12": [candidate("111111", 1), candidate("444444", 2), candidate("555555", 3)],
        })
        repository = FakeRepository()
        runtime = OrderbookConditionResearchRuntime(repository=repository, condition_search=search)

        first = runtime.poll_once(at=AT)
        second = runtime.poll_once(at=AT + timedelta(seconds=10))

        self.assertEqual(search.resolved, [SELL, BUY])
        self.assertEqual(first, {SELL: 3, BUY: 3})
        self.assertEqual(second, {SELL: 3, BUY: 3})
        self.assertEqual(len(repository.rows), 12)
        transition_rows = [key for key in repository.rows if key[3] == "111111"]
        self.assertEqual(len(transition_rows), 4)
        self.assertEqual({key[1] for key in transition_rows}, {SELL, BUY})

    def test_same_poll_duplicate_is_one_row_and_raw_values_are_preserved(self):
        duplicate = candidate("111111", 2)
        search = FakeSearch({"11": [candidate("111111", 1), duplicate], "12": []})
        repository = FakeRepository()
        runtime = OrderbookConditionResearchRuntime(repository=repository, condition_search=search)

        runtime.poll_once(at=AT)

        self.assertEqual(len(repository.rows), 1)
        stored = next(iter(repository.rows.values()))
        self.assertEqual(stored["result_order"], 1)
        self.assertEqual(stored["raw_payload"]["price"], "12345")
        self.assertEqual(stored["raw_payload"]["volume"], "67890")

    def test_zero_results_create_no_rows(self):
        repository = FakeRepository()
        runtime = OrderbookConditionResearchRuntime(
            repository=repository, condition_search=FakeSearch({"11": [], "12": []}),
        )

        counts = runtime.poll_once(at=AT)

        self.assertEqual(counts, {SELL: 0, BUY: 0})
        self.assertEqual(repository.rows, {})

    def test_configured_collection_window_is_enforced(self):
        search = FakeSearch({"11": [candidate("111111", 1)], "12": []})
        runtime = OrderbookConditionResearchRuntime(
            repository=FakeRepository(), condition_search=search,
            start_time=time(9, 0), end_time=time(15, 20),
        )

        self.assertEqual(runtime.poll_once(at=datetime(2026, 9, 22, 8, 59, 59)), {})
        self.assertEqual(runtime.poll_once(at=datetime(2026, 9, 22, 15, 20, 1)), {})
        self.assertEqual(search.resolved, [])
        self.assertEqual(OrderbookConditionResearchRuntime.parse_time("09:00"), time(9, 0))

    def test_migration_and_runner_remain_research_only(self):
        root = Path(__file__).parents[1]
        migration = (root / "database/migrations/20260922_orderbook_condition_research_hit.sql").read_text(encoding="utf-8").upper()
        runtime_source = (root / "src/orderbook_condition_research/runtime.py").read_text(encoding="utf-8").lower()
        runner_source = (root / "scripts/runtime/run_flow_raw_collector.py").read_text(encoding="utf-8")

        self.assertIn("CREATE TABLE IF NOT EXISTS ORDERBOOK_CONDITION_RESEARCH_HIT", migration)
        self.assertIn("UNIQUE (POLL_TIME, CONDITION_NAME, CONDITION_SEQ, STOCK_CODE)", migration)
        self.assertNotIn("ALTER TABLE RAW_", migration)
        self.assertNotIn("UPDATE RAW_", migration)
        self.assertNotIn("DELETE FROM RAW_", migration)
        self.assertNotIn("websockets.connect", runtime_source)
        self.assertNotIn("live_enabled", runtime_source)
        self.assertEqual(runner_source.count("client = KISClient()"), 1)


if __name__ == "__main__":
    unittest.main()
