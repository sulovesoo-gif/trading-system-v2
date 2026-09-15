"""Optional isolated PostgreSQL integration; NOT a Timescale extension test.

Real tables/streaming/locks/transactions are exercised. The drop_chunks shim is
explicitly a test double. Set RETENTION_TEST_DSN to local minute_ma_retention_test.
"""
from dataclasses import asdict
from datetime import datetime
import os
from pathlib import Path
import re
import unittest
from uuid import UUID

import psycopg
from psycopg.conninfo import conninfo_to_dict

from src.flow_raw.realtime_minute import ExecutionTick, build_realtime_minute_bars
from src.minute_ma.integrated_raw_retention import PostgresRetentionStore, RetentionBlocked, run_retention

DSN = os.getenv("RETENTION_TEST_DSN")
NOW = datetime(2026, 9, 15, 21, 30)
CID = UUID("00000000-0000-0000-0000-000000000001")


@unittest.skipUnless(DSN, "isolated local PostgreSQL DSN not configured")
class PostgresRetentionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        info = conninfo_to_dict(DSN)
        if info.get("host") != "127.0.0.1" or info.get("dbname") != "minute_ma_retention_test":
            raise RuntimeError("REFUSE non-test database")

    def setUp(self):
        self.c = psycopg.connect(DSN)
        self.c.execute("SET TIME ZONE 'Asia/Seoul'")
        self.c.execute("SET statement_timeout='10s'")
        # All fixtures are rolled back in tearDown. No production credentials.
        text = (Path(__file__).resolve().parents[1] /
                "database/migrations/20260902_minute_ma_integrated_realtime_additive.sql").read_text()
        tables = re.findall(r"CREATE TABLE IF NOT EXISTS .*?\n\);", text, re.S)
        for table in tables:
            self.c.execute(table)
        self.c.execute("""INSERT INTO minute_ma_integrated_ws_connection
            (connection_id,collector_instance_id,connected_at,status)
            VALUES(%s,%s,'2026-09-10 08:00','CONNECTED')""", (CID, CID))
        for day in (10, 11, 14, 15):
            self.c.execute(f"CREATE TABLE public.ret_chunk_{day} () INHERITS (raw_minute_ma_integrated_execution)")
            for stock in ("000660", "005930"):
                ticks = []
                for minute in (0, 1):
                    ts = datetime(2026, 9, day, 9, minute, 1)
                    seq = day * 10000 + minute * 10 + (stock == "005930")
                    self.c.execute(f"""INSERT INTO public.ret_chunk_{day}
                        (received_at,source_event_time,business_date,stock_code,connection_id,
                         collector_instance_id,receive_sequence,event_index,payload_hash,
                         current_price,execution_volume,accumulated_volume,raw_values,raw_payload)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,0,'hash',100,1,%s,'[]','fixture')""",
                        (ts, ts, ts.date(), stock, CID, CID, seq, 100 + minute))
                    ticks.append(ExecutionTick(stock, ts, datetime(2026, 9, 10, 8), seq, 0,
                                               ts, 100, 1, 100 + minute, str(CID)))
                for bar in build_realtime_minute_bars(ticks, now=NOW):
                    values = asdict(bar)
                    values["quality_reasons"] = list(values["quality_reasons"])
                    self.c.execute(f"INSERT INTO minute_ma_integrated_realtime_minute_bar ({','.join(values)})"
                                   f" VALUES({','.join(['%s'] * len(values))})", tuple(values.values()))
        self.c.execute("CREATE SCHEMA timescaledb_information")
        self.c.execute("""CREATE VIEW timescaledb_information.dimensions AS SELECT
            'public'::text hypertable_schema,'raw_minute_ma_integrated_execution'::text hypertable_name,
            'received_at'::text column_name,'timestamp without time zone'::text column_type""")
        self.c.execute("""CREATE VIEW timescaledb_information.chunks AS SELECT
            'public'::text hypertable_schema,'raw_minute_ma_integrated_execution'::text hypertable_name,
            'public'::text chunk_schema,'ret_chunk_'||d::text chunk_name,
            (timestamp '2026-09-01'+(d-1)*interval '1 day') AT TIME ZONE 'UTC' range_start,
            (timestamp '2026-09-01'+d*interval '1 day') AT TIME ZONE 'UTC' range_end
            FROM (VALUES(10),(11),(14),(15)) v(d)
            WHERE to_regclass('public.ret_chunk_'||d::text) IS NOT NULL""")
        self.c.execute("""CREATE FUNCTION public.drop_chunks(relation regclass,
            older_than timestamp,newer_than timestamp) RETURNS SETOF text LANGUAGE plpgsql AS $$
            DECLARE r record;
            BEGIN
              IF relation::text <> 'raw_minute_ma_integrated_execution' THEN RAISE EXCEPTION 'wrong target'; END IF;
              FOR r IN SELECT chunk_schema,chunk_name FROM timescaledb_information.chunks
                WHERE range_end AT TIME ZONE 'UTC' <= older_than
                  AND range_start AT TIME ZONE 'UTC' >= newer_than
              LOOP
                EXECUTE format('DROP TABLE %I.%I',r.chunk_schema,r.chunk_name);
                RETURN NEXT r.chunk_schema||'.'||r.chunk_name;
              END LOOP;
            END $$""")
        self.store = PostgresRetentionStore(self.c)

    def tearDown(self):
        self.c.rollback()
        self.c.close()

    def test_streaming_bar_sql_and_exact_drop_rerun(self):
        result = run_retention(self.store, now=NOW, apply=True)
        self.assertEqual(result["verified_minutes"], 8)
        self.assertEqual(result["actual_removed_chunks"], ["public.ret_chunk_10", "public.ret_chunk_11"])
        self.assertEqual(self.c.execute("SELECT count(*) FROM raw_minute_ma_integrated_execution").fetchone()[0], 8)
        self.assertEqual(self.c.execute("SELECT count(*) FROM minute_ma_integrated_realtime_minute_bar").fetchone()[0], 16)
        self.assertEqual(run_retention(self.store, now=NOW, apply=True)["status"], "SKIP")

    def test_timezone_metadata_recovers_naive_midnight(self):
        self.assertEqual(self.store.chunks()[0].start, datetime(2026, 9, 10))

    def test_late_raw_causes_no_chunk_drop(self):
        self.c.execute("UPDATE ret_chunk_11 SET current_price=101 WHERE stock_code='000660'")
        with self.assertRaisesRegex(RetentionBlocked, "BAR_MISMATCH"):
            run_retention(self.store, now=NOW, apply=True)
        self.assertEqual(len(self.store.chunks()), 4)

    def test_missing_bar_causes_no_chunk_drop(self):
        self.c.execute("DELETE FROM minute_ma_integrated_realtime_minute_bar WHERE bar_time='2026-09-11 09:01'")
        with self.assertRaisesRegex(RetentionBlocked, "BAR_MISSING"):
            run_retention(self.store, now=NOW, apply=True)
        self.assertEqual(len(self.store.chunks()), 4)

    def test_source_day_protection_even_in_old_received_chunk(self):
        self.c.execute("UPDATE ret_chunk_10 SET business_date='2026-09-15',source_event_time='2026-09-15 09:00'")
        with self.assertRaisesRegex(RetentionBlocked, "PROTECTED_OR_CROSS_DAY_RAW"):
            run_retention(self.store, now=NOW, apply=True)
        self.assertEqual(len(self.store.chunks()), 4)

    def test_drop_error_rolls_back_prior_drops(self):
        original = self.store.drop
        def broken(chunk):
            if chunk.name == "ret_chunk_11":
                self.c.execute("SELECT 1/0")
            return original(chunk)
        self.store.drop = broken
        with self.assertRaises(psycopg.errors.DivisionByZero):
            with self.c.transaction():
                run_retention(self.store, now=NOW, apply=True)
        self.assertEqual(len(self.store.chunks()), 4)

    def test_dry_run_no_drop(self):
        # Fixtures remain local to this transaction; savepoint checks pure read path.
        result = run_retention(self.store, now=NOW, apply=False)
        self.assertEqual(result["status"], "DRY_RUN_PASS")
        self.assertEqual(len(self.store.chunks()), 4)

    def test_migration_sql_future_interval_and_rerun(self):
        self.c.execute("CREATE TABLE ret_interval(value interval)")
        self.c.execute("""CREATE FUNCTION set_chunk_time_interval(regclass,interval)
            RETURNS void LANGUAGE sql AS $$ INSERT INTO ret_interval VALUES($2) $$""")
        migration = (Path(__file__).resolve().parents[1] /
                     "database/migrations/20260915_minute_ma_integrated_raw_chunk_interval.sql").read_text()
        before = self.store.chunks()
        self.c.execute(migration, prepare=False)
        self.c.execute(migration, prepare=False)
        self.assertEqual(self.c.execute("SELECT count(*) FROM ret_interval WHERE value=interval '1 day'").fetchone()[0], 2)
        self.assertEqual([(c.start, c.end) for c in before], [(c.start, c.end) for c in self.store.chunks()])

    def test_migration_wrong_dimension_fails_before_interval_call(self):
        self.c.execute("CREATE OR REPLACE VIEW timescaledb_information.dimensions AS SELECT 'public'::text hypertable_schema,"
                       "'raw_minute_ma_integrated_execution'::text hypertable_name,'unexpected'::text column_name,"
                       "'timestamp without time zone'::text column_type")
        migration = (Path(__file__).resolve().parents[1] /
                     "database/migrations/20260915_minute_ma_integrated_raw_chunk_interval.sql").read_text()
        with self.assertRaisesRegex(psycopg.errors.RaiseException, "Unexpected INTEGRATED RAW"):
            with self.c.transaction(): self.c.execute(migration, prepare=False)


if __name__ == "__main__": unittest.main()
