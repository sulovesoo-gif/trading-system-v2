from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime

from src.repository.raw_repository import RawRepository, RawRepositoryError, RawWriteResult
from src.repository.raw_specs import RawTable, get_raw_spec
from src.service.raw_ingestion_service import RawIngestionService


class FakeCursor:
    def __init__(self, returned_rows=(), error=None):
        self.returned_rows = list(returned_rows)
        self.error = error
        self.query = None
        self.params = None

    def execute(self, query, params):
        self.query, self.params = query, params
        if self.error:
            raise self.error

    def fetchall(self):
        return self.returned_rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.transaction_rolled_back = False

    @contextmanager
    def transaction(self):
        try:
            yield
        except Exception:
            self.transaction_rolled_back = True
            raise

    def cursor(self):
        return self.cursor_value


class FakePool:
    def __init__(self, connection):
        self.connection_value = connection

    @contextmanager
    def connection(self):
        yield self.connection_value


def program_row():
    spec = get_raw_spec(RawTable.PROGRAM)
    row = {column: None for column in spec.columns}
    row.update({
        "snapshot_time": datetime(2026, 7, 29, 10, 0),
        "collected_at": datetime(2026, 7, 29, 10, 0),
        "data_source": "KIS", "market_code": "KOSPI", "collect_cycle": "1MIN",
        "stock_code": "000660", "raw_payload": {"stck_prpr": "1"},
    })
    return row


class RawRepositoryTest(unittest.TestCase):
    def create_repository(self, *, returned_rows=(), error=None):
        self.cursor = FakeCursor(returned_rows, error)
        self.connection = FakeConnection(self.cursor)
        return RawRepository(FakePool(self.connection), jsonb_factory=lambda value: ("JSONB", value))

    def test_empty_list_returns_zero_counts_without_database_call(self):
        repository = self.create_repository()
        self.assertEqual(repository.save(RawTable.PROGRAM, []), RawWriteResult("raw_program", 0, 0, 0))
        self.assertIsNone(self.cursor.query)

    def test_multiple_rows_use_returning_for_exact_insert_and_duplicate_counts(self):
        repository = self.create_repository(returned_rows=[(1,), (1,)])
        result = repository.save(RawTable.PROGRAM, [program_row(), program_row(), program_row()])
        self.assertEqual(result, RawWriteResult("raw_program", 3, 2, 1))
        self.assertIn("ON CONFLICT DO NOTHING RETURNING 1", self.cursor.query)
        self.assertIn("raw_program", self.cursor.query)
        self.assertEqual(len(self.cursor.params), len(get_raw_spec(RawTable.PROGRAM).columns) * 3)
        self.assertEqual(self.cursor.params[-1], ("JSONB", {"stck_prpr": "1"}))

    def test_single_row_uses_ddl_column_order_and_excludes_created_at(self):
        repository = self.create_repository(returned_rows=[(1,)])
        repository.save(RawTable.PROGRAM, program_row())
        spec = get_raw_spec(RawTable.PROGRAM)
        self.assertIn(", ".join(spec.columns), self.cursor.query)
        self.assertNotIn("created_at", self.cursor.query)

    def test_unknown_or_missing_collector_field_fails_before_insert(self):
        repository = self.create_repository()
        row = program_row()
        row.pop("stock_code")
        row["unknown"] = "x"
        with self.assertRaises(RawRepositoryError):
            repository.save(RawTable.PROGRAM, row)
        self.assertIsNone(self.cursor.query)

    def test_transaction_is_rolled_back_on_insert_error(self):
        repository = self.create_repository(error=RuntimeError("database failed"))
        with self.assertRaises(RawRepositoryError):
            repository.save(RawTable.PROGRAM, program_row())
        self.assertTrue(self.connection.transaction_rolled_back)

    def test_ingestion_service_forwards_raw_values_without_mutation(self):
        repository = self.create_repository(returned_rows=[(1,)])
        row = program_row()
        result = RawIngestionService(repository).store(RawTable.PROGRAM, row)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(row["raw_payload"], {"stck_prpr": "1"})
