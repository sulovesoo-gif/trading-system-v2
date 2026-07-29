from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from src.repository.database import DatabaseConfigurationError, DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.repository.raw_specs import RawTable, get_raw_spec


ROOT = Path(__file__).parents[2]


def integration_ready() -> tuple[bool, str]:
    if os.getenv("DB_INTEGRATION_TEST") != "1":
        return False, "DB_INTEGRATION_TEST=1 is required; destructive DDL is disabled."
    required = ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        return False, f"Missing database environment variables: {', '.join(missing)}"
    database_name = os.environ["DB_NAME"].lower()
    if "test" not in database_name and not database_name.endswith("_test"):
        return False, "DB_NAME must contain 'test' because integration DDL drops RAW tables."
    return True, ""


READY, REASON = integration_ready()


@unittest.skipUnless(READY, REASON)
class TimescaleRawRepositoryIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = create_connection_pool(DatabaseSettings.from_environment())
        with cls.pool.connection() as connection:
            with connection.cursor() as cursor:
                for ddl_name in ("04_backfill_job.sql", "05_backfill_segment.sql"):
                    cursor.execute((ROOT / "database" / "ddl" / ddl_name).read_text(encoding="utf-8"))
                for ddl in sorted((ROOT / "database" / "ddl").glob("1[0-7]_raw_*.sql")):
                    cursor.execute(ddl.read_text(encoding="utf-8"))
                cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'timescaledb'")
                cls.timescaledb_extension = cursor.fetchone()
                cursor.execute("SELECT hypertable_name FROM timescaledb_information.hypertables")
                cls.hypertables = {row[0] for row in cursor.fetchall()}
                cursor.execute("SHOW TIME ZONE")
                cls.time_zone = cursor.fetchone()[0]
        cls.repository = RawRepository(cls.pool)

    @classmethod
    def tearDownClass(cls):
        cls.pool.close()

    def test_connection_timezone_and_conflict_policy(self):
        self.assertEqual(self.timescaledb_extension, ("timescaledb",))
        self.assertEqual(self.hypertables, {table.value for table in RawTable})
        self.assertEqual(self.time_zone, "Asia/Seoul")
        for offset, table in enumerate(RawTable):
            row = self._row(table, offset)
            first = self.repository.save(table, row)
            second = self.repository.save(table, row)
            self.assertEqual((first.requested_count, first.inserted_count), (1, 1), table.value)
            self.assertEqual((second.requested_count, second.duplicate_count), (1, 1), table.value)

    def test_multiple_rows_jsonb_timestamp_precision_and_rollback(self):
        first = self._row(RawTable.PROGRAM, 100)
        second = self._row(RawTable.PROGRAM, 101)
        result = self.repository.save(RawTable.PROGRAM, [first, second])
        self.assertEqual((result.requested_count, result.inserted_count, result.duplicate_count), (2, 2, 0))
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT raw_payload, snapshot_time FROM raw_program WHERE collect_cycle = 'TEST' AND stock_code = '000660' AND snapshot_time = %s", (first["snapshot_time"],))
                payload, snapshot_time = cursor.fetchone()
        self.assertEqual(payload, first["raw_payload"])
        self.assertEqual(snapshot_time.microsecond, 123000)
        invalid = dict(self._row(RawTable.PROGRAM, 102))
        invalid["data_source"] = None
        with self.assertRaises(Exception):
            self.repository.save(RawTable.PROGRAM, [self._row(RawTable.PROGRAM, 103), invalid])
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM raw_program WHERE collect_cycle = 'TEST' AND snapshot_time = %s", (self._row(RawTable.PROGRAM, 103)["snapshot_time"],))
                self.assertEqual(cursor.fetchone()[0], 0)

    def test_connection_pool_reuses_an_available_connection(self):
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                first_backend_pid = cursor.fetchone()[0]
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                second_backend_pid = cursor.fetchone()[0]
        self.assertEqual(first_backend_pid, second_backend_pid)

    @staticmethod
    def _row(table: RawTable, offset: int) -> dict[str, object]:
        spec = get_raw_spec(table)
        timestamp = datetime(2026, 7, 29, 10, 0, 0, 123000) + timedelta(minutes=offset)
        row = {column: None for column in spec.columns}
        row.update({"collected_at": timestamp, "data_source": "KIS", "market_code": "TEST", "collect_cycle": "TEST", "raw_payload": {"table": table.value, "offset": offset}})
        if "trading_venue" in row:
            row["trading_venue"] = "KRX"
        if "snapshot_time" in row:
            row["snapshot_time"] = timestamp
        if "bar_time" in row:
            row["bar_time"] = timestamp
        if "trade_date" in row:
            row["trade_date"] = date(2026, 7, 29) + timedelta(days=offset)
        if "stock_code" in row:
            row["stock_code"] = "000660"
        if "futures_code" in row:
            row["futures_code"] = "A01609"
        return row
