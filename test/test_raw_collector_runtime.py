from __future__ import annotations

import ast
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.collector.raw.runtime import RawCollectorRuntime, collection_window_active
from src.collector.raw.shadow import RawShadowComparator, semantic_equal, unexpected_minute_gaps
from src.repository.common_code_repository import ApiScheduleConfig, StockConfig
from src.repository.raw_specs import RawTable, get_raw_spec


def minute_row(stock_code: str, venue: str, at: datetime) -> dict[str, object]:
    return {
        "bar_time": at, "collected_at": at, "data_source": "KIS", "market_code": "KOSPI", "trading_venue": venue,
        "collect_cycle": "1MIN", "stock_code": stock_code, "open_price": Decimal("1"), "high_price": Decimal("2"),
        "low_price": Decimal("1"), "close_price": Decimal("2"), "previous_close_price": Decimal("1"), "volume": 10,
        "accumulated_amount": Decimal("20"), "raw_payload": {"z": "2", "a": "1"},
    }


class FakeCodes:
    def __init__(self, stocks, *, program_due=False, execution_due=False):
        self.stocks = stocks
        self.program_due = program_due
        self.execution_due = execution_due

    def enabled_minute_stocks(self):
        return self.stocks

    def api_schedule(self, code):
        due = self.program_due if code == "STOCK_PROGRAM_1MIN" else self.execution_due
        second = 2 if code == "STOCK_PROGRAM_1MIN" else 0
        return ApiScheduleConfig(code, "MIN" if code == "STOCK_PROGRAM_1MIN" else "SEC", 1 if code == "STOCK_PROGRAM_1MIN" else 5, second, "08:00", "20:00", due)


class FakeMinuteCollector:
    def __init__(self, failing=()):
        self.failing = set(failing)
        self.calls = []

    def collect(self, *, stock_code, market_code, trading_venue, input_hour):
        self.calls.append(stock_code)
        if stock_code in self.failing:
            raise RuntimeError("network")
        moment = datetime(2026, 8, 17, int(input_hour[:2]), int(input_hour[2:4]))
        return [minute_row(stock_code, trading_venue, moment - timedelta(minutes=1)), minute_row(stock_code, trading_venue, moment)]


class FakeRowsCollector:
    def __init__(self, table):
        self.table = table
        self.calls = []

    def collect(self, *, stock_code, market_code, trading_venue, **_kwargs):
        self.calls.append(stock_code)
        row = {column: None for column in get_raw_spec(self.table).columns}
        row.update({"snapshot_time": datetime(2026, 8, 17, 10, 2), "collected_at": datetime(2026, 8, 17, 10, 2),
                    "data_source": "KIS", "market_code": market_code, "trading_venue": trading_venue,
                    "collect_cycle": "1MIN" if self.table is RawTable.PROGRAM else "5SEC", "stock_code": stock_code,
                    "raw_payload": {"value": "same"}})
        return [row]


class FakeIngestion:
    def __init__(self): self.calls = []
    def store(self, table, rows): self.calls.append((table, rows))


def stocks():
    return [
        StockConfig("000660", "SK", "STOCK", True, True, True, "INTEGRATED", True),
        StockConfig("005930", "Samsung", "STOCK", True, True, True, "INTEGRATED", False),
    ]


class RawCollectorRuntimeTest(unittest.TestCase):
    def runtime(self, *, program_due=False, execution_due=False, failing=()):
        self.ingestion = FakeIngestion()
        self.minute = FakeMinuteCollector(failing)
        self.program = FakeRowsCollector(RawTable.PROGRAM)
        self.execution = FakeRowsCollector(RawTable.STOCK_EXECUTION)
        return RawCollectorRuntime(codes=FakeCodes(stocks(), program_due=program_due, execution_due=execution_due), raw_ingestion=self.ingestion,
                                   minute_collector=self.minute, program_collector=self.program, execution_collector=self.execution)

    def test_schedule_matches_existing_collection_window_and_minute_second(self):
        runtime = self.runtime()
        self.assertFalse(collection_window_active(datetime(2026, 8, 16, 10, 1, 1)))
        self.assertTrue(runtime.scheduled(datetime(2026, 8, 17, 10, 1, 1)))
        self.assertTrue(runtime.scheduled(datetime(2026, 8, 17, 10, 1, 5)))
        self.assertFalse(runtime.scheduled(datetime(2026, 8, 17, 10, 1, 3)))

    def test_stock_targets_come_only_from_common_code_and_shadow_does_not_write(self):
        tick = self.runtime().collect_tick(now=datetime(2026, 8, 17, 10, 1, 1), store_records=False)
        self.assertEqual(self.minute.calls, ["000660", "005930"])
        self.assertEqual({item.stock_code for item in tick.records}, {"000660", "005930"})
        self.assertEqual(self.ingestion.calls, [])

    def test_program_tick_preserves_legacy_continue_and_execution_contract(self):
        tick = self.runtime(program_due=True, execution_due=True).collect_tick(now=datetime(2026, 8, 17, 10, 2, 2), store_records=False)
        self.assertEqual(self.program.calls, ["000660"])
        self.assertEqual(self.execution.calls, [])
        self.assertEqual(self.minute.calls, [])
        self.assertEqual([item.table for item in tick.records], [RawTable.PROGRAM])

    def test_one_stock_failure_does_not_block_other_stock(self):
        tick = self.runtime(failing={"000660"}).collect_tick(now=datetime(2026, 8, 17, 10, 1, 1), store_records=False)
        self.assertEqual(self.minute.calls, ["000660", "005930"])
        self.assertEqual([item.stock_code for item in tick.records], ["005930"])
        self.assertEqual([(item.table, item.stock_code) for item in tick.failures], [(RawTable.STOCK_MINUTE, "000660")])

    def test_same_session_gap_is_reported_but_session_boundary_is_not(self):
        self.assertEqual(unexpected_minute_gaps([datetime(2026, 8, 17, 8, 48), datetime(2026, 8, 17, 8, 49), datetime(2026, 8, 17, 9, 0)]), [])
        self.assertEqual(unexpected_minute_gaps([datetime(2026, 8, 17, 9, 0), datetime(2026, 8, 17, 9, 2)]), [(datetime(2026, 8, 17, 9, 0), datetime(2026, 8, 17, 9, 2))])


class FakeCursor:
    def __init__(self, row): self.row = row; self.query = None; self.params = None
    def execute(self, query, params): self.query, self.params = query, params
    def fetchone(self): return self.row
    def __enter__(self): return self
    def __exit__(self, *_args): return False


class FakeConnection:
    def __init__(self, cursor): self.cursor_value = cursor
    def cursor(self): return self.cursor_value
    def __enter__(self): return self
    def __exit__(self, *_args): return False


class FakePool:
    def __init__(self, cursor): self.cursor = cursor
    @contextmanager
    def connection(self): yield FakeConnection(self.cursor)


class RawShadowComparatorTest(unittest.TestCase):
    def test_comparison_uses_primary_key_ignores_collection_time_and_compares_payload_semantically(self):
        actual = minute_row("000660", "INTEGRATED", datetime(2026, 8, 17, 10, 0))
        expected = dict(actual); expected["collected_at"] = datetime(2026, 8, 17, 10, 0, 5); expected["raw_payload"] = {"a": "1", "z": "2"}
        cursor = FakeCursor(tuple(expected[column] for column in get_raw_spec(RawTable.STOCK_MINUTE).columns))
        result = RawShadowComparator(FakePool(cursor)).compare(RawTable.STOCK_MINUTE, actual)
        self.assertTrue(result.expected_found); self.assertTrue(result.matched); self.assertEqual(result.mismatched_columns, ())
        self.assertIn("WHERE bar_time=%s", cursor.query)

    def test_comparison_reports_canonical_field_difference(self):
        actual = minute_row("000660", "INTEGRATED", datetime(2026, 8, 17, 10, 0))
        expected = dict(actual); expected["high_price"] = Decimal("3")
        cursor = FakeCursor(tuple(expected[column] for column in get_raw_spec(RawTable.STOCK_MINUTE).columns))
        result = RawShadowComparator(FakePool(cursor)).compare(RawTable.STOCK_MINUTE, actual)
        self.assertFalse(result.matched); self.assertEqual(result.mismatched_columns, ("high_price",))


class RawRuntimeDependencyTest(unittest.TestCase):
    def test_raw_runtime_import_graph_excludes_strategy_and_alert_layers(self):
        root = Path(__file__).resolve().parents[1]
        forbidden = ("src.analysis", "src.service.research", "src.service.ntfy", "src.service.email", "order", "position", "capital")
        for relative in ("src/collector/raw/runtime.py", "scripts/realtime/run_raw_collector_shadow.py"):
            tree = ast.parse((root / relative).read_text(encoding="utf-8"))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom): imports.append(node.module or "")
                if isinstance(node, ast.Import): imports.extend(item.name for item in node.names)
            self.assertTrue(all(not name.startswith(forbidden) for name in imports), (relative, imports))


if __name__ == "__main__":
    unittest.main()
