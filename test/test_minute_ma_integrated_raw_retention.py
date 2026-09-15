from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path
import unittest

from src.flow_raw.realtime_minute import ExecutionTick, build_realtime_minute_bars
from src.minute_ma.integrated_raw_retention import (
    Chunk, RetentionBlocked, eligible_chunks, retention_cutoff, run_retention, verify_bar,
)

NOW = datetime(2026, 9, 15, 21, 30)
DAYS = {s: (date(2026, 9, 15), date(2026, 9, 14)) for s in ("000660", "005930")}


def chunk(day, length=1):
    start = datetime(2026, 9, day)
    return Chunk("_timescaledb_internal", f"chunk_{day}", start, start + timedelta(days=length), 1000)


class FakeStore:
    def __init__(self):
        self.items = [chunk(11), chunk(14), chunk(15)]
        self.days = DAYS
        self.drops = []
        self.verified = False
        self.locked = False
        self.failure = None

    def check_dimension(self): pass
    def data_days(self, today): return self.days
    def chunks(self): return list(self.items)
    def acquire(self): pass
    def lock_candidates(self, candidates): self.locked = True

    def verify_candidates(self, candidates, cutoff, now):
        if self.failure:
            raise self.failure
        self.verified = True
        return 100

    def drop(self, item):
        assert self.locked and self.verified
        self.drops.append(item.qualified)
        self.items.remove(item)
        return [item.qualified]


def tick(minute=0, second=1, price=100, volume=100, **changes):
    values = dict(stock_code="000660", source_event_time=datetime(2026, 9, 11, 9, minute, second),
                  connection_connected_at=datetime(2026, 9, 11, 8), receive_sequence=minute * 100 + second,
                  event_index=0, received_at=datetime(2026, 9, 11, 9, minute, second),
                  current_price=price, execution_volume=1, accumulated_volume=volume,
                  connection_id="connection")
    values.update(changes)
    return ExecutionTick(**values)


def stored(ticks, minute=1):
    bar = next(b for b in build_realtime_minute_bars(ticks, now=NOW) if b.bar_time.minute == minute)
    return {**asdict(bar), "raw_source": "KIS_H0UNCNT0_INTEGRATED"}


class RetentionPlanTest(unittest.TestCase):
    def test_d0_and_d1_preserved_d2_eligible(self):
        s = FakeStore()
        r = run_retention(s, now=NOW, apply=True)
        self.assertEqual(r["status"], "COMPLETE")
        self.assertEqual(s.items, [chunk(14), chunk(15)])
        self.assertEqual(s.drops, [chunk(11).qualified])

    def test_monday_preserves_friday(self):
        days = {s: (date(2026, 9, 14), date(2026, 9, 11)) for s in DAYS}
        self.assertEqual(retention_cutoff(days, date(2026, 9, 14)), datetime(2026, 9, 11))

    def test_holiday_preserves_last_two_actual_days(self):
        days = {s: (date(2026, 9, 11), date(2026, 9, 10)) for s in DAYS}
        self.assertEqual(retention_cutoff(days, date(2026, 9, 13)), datetime(2026, 9, 10))

    def test_lagging_symbol_makes_cutoff_older(self):
        days = {**DAYS, "000660": (date(2026, 9, 11), date(2026, 9, 10))}
        self.assertEqual(retention_cutoff(days, NOW.date()), datetime(2026, 9, 10))

    def test_future_or_missing_data_day_blocks(self):
        for days in ({}, {s: (date(2026, 9, 16), date(2026, 9, 14)) for s in DAYS}):
            with self.assertRaises(RetentionBlocked): retention_cutoff(days, NOW.date())

    def test_exact_end_cutoff_eligible_straddler_not(self):
        cutoff = datetime(2026, 9, 14)
        self.assertEqual(eligible_chunks([chunk(13), chunk(14)], cutoff), [chunk(13)])
        self.assertEqual(eligible_chunks([chunk(10, 7)], cutoff), [])

    def test_09_boundary_is_not_misinterpreted_as_midnight(self):
        c = replace(chunk(13), end=datetime(2026, 9, 14, 9))
        self.assertEqual(eligible_chunks([c], datetime(2026, 9, 14)), [])

    def test_overlapping_chunks_block(self):
        with self.assertRaises(RetentionBlocked): eligible_chunks([chunk(10, 7), chunk(11)], NOW)

    def test_bar_verification_failure_no_drop(self):
        s = FakeStore(); s.failure = RetentionBlocked("BAR_MISSING")
        with self.assertRaises(RetentionBlocked): run_retention(s, now=NOW, apply=True)
        self.assertEqual(s.drops, [])

    def test_db_failure_no_drop(self):
        s = FakeStore(); s.failure = RuntimeError("DB timeout")
        with self.assertRaises(RuntimeError): run_retention(s, now=NOW, apply=True)
        self.assertEqual(s.drops, [])

    def test_rerun_and_already_dropped_chunks_safe(self):
        s = FakeStore()
        run_retention(s, now=NOW, apply=True)
        self.assertEqual(run_retention(s, now=NOW, apply=True)["status"], "SKIP")
        self.assertEqual(len(s.drops), 1)

    def test_dry_run_does_not_lock_or_drop(self):
        s = FakeStore()
        self.assertEqual(run_retention(s, now=NOW, apply=False)["status"], "DRY_RUN_PASS")
        self.assertEqual(s.drops, []); self.assertFalse(s.locked)

    def test_intraday_and_midnight_block(self):
        for hour in (0, 9, 20, 21):
            with self.assertRaises(RetentionBlocked):
                run_retention(FakeStore(), now=NOW.replace(hour=hour, minute=0), apply=True)

    def test_all_candidates_verified_before_first_drop(self):
        s = FakeStore(); s.items.insert(0, chunk(10)); s.failure = RetentionBlocked("second candidate bad")
        with self.assertRaises(RetentionBlocked): run_retention(s, now=NOW, apply=True)
        self.assertEqual(s.drops, [])

    def test_concurrent_chunk_change_blocks(self):
        s = FakeStore()
        def lock(candidates): s.items.insert(0, chunk(10))
        s.lock_candidates = lock
        with self.assertRaisesRegex(RetentionBlocked, "CANDIDATE_SET_CHANGED"):
            run_retention(s, now=NOW, apply=True)

    def test_unexpected_drop_result_is_error_for_transaction_rollback(self):
        s = FakeStore(); s.drop = lambda c: ["wrong_chunk"]
        with self.assertRaisesRegex(RetentionBlocked, "UNEXPECTED_DROP_RESULT"):
            run_retention(s, now=NOW, apply=True)


class BarVerificationTest(unittest.TestCase):
    def setUp(self):
        self.ticks = [tick(0), tick(1, price=101, volume=110), tick(2, price=102, volume=120)]
        self.bar = stored(self.ticks)
        self.target = datetime(2026, 9, 11, 9, 1)

    def check(self, bar=None, ticks=None):
        verify_bar(ticks or self.ticks, self.target, "000660", self.bar if bar is None else bar,
                   now=NOW, grace_ms=2000)

    def test_matching_completed_bar(self): self.check()

    def test_missing_bar_block(self):
        with self.assertRaisesRegex(RetentionBlocked, "BAR_MISSING"):
            verify_bar(self.ticks, self.target, "000660", None, now=NOW, grace_ms=2000)

    def test_late_tick_blocks(self):
        with self.assertRaisesRegex(RetentionBlocked, "BAR_MISMATCH"):
            self.check(ticks=self.ticks + [tick(1, second=2, volume=111)])

    def test_price_volume_and_counts_mismatch_block(self):
        for field in ("open_price", "volume", "event_count", "message_count", "execution_volume_sum"):
            with self.subTest(field=field), self.assertRaises(RetentionBlocked):
                self.check(bar={**self.bar, field: self.bar[field] + 1})

    def test_incomplete_unknown_quality_blocks(self):
        with self.assertRaises(RetentionBlocked):
            self.check(bar={**self.bar, "quality_status": "INCOMPLETE", "quality_reasons": ["UNKNOWN"]})

    def test_actual_gap_and_regression_block(self):
        for changes in ({"source_gap_flag": True}, {"event_time_regression_flag": True},
                        {"accumulated_volume": 1}):
            values = [self.ticks[0], replace(self.ticks[1], **changes), self.ticks[2]]
            with self.subTest(changes=changes), self.assertRaises(RetentionBlocked):
                self.check(ticks=values, bar=stored(values))

    def test_first_minute_boundary_is_finalized_not_relabelled(self):
        values = [tick(0), tick(1)]
        bar = stored(values, minute=0)
        self.assertEqual(bar["quality_status"], "INCOMPLETE")
        verify_bar(values, datetime(2026, 9, 11, 9), "000660", bar, now=NOW, grace_ms=2000)
        self.assertEqual(bar["quality_status"], "INCOMPLETE")

    def test_grace_finalized_last_minute(self):
        values = self.ticks[:2]
        self.check(ticks=values, bar=stored(values))

    def test_unfinalized_timestamp_blocks(self):
        with self.assertRaises(RetentionBlocked): self.check(bar={**self.bar, "finalized_at": self.target})

    def test_duplicate_only_minute_has_no_synthetic_bar(self):
        values = [replace(tick(1), duplicate_flag=True)]
        verify_bar(values, self.target, "000660", None, now=NOW, grace_ms=2000)


class IsolationContractTest(unittest.TestCase):
    def test_only_drop_target_and_no_row_dml(self):
        root = Path(__file__).resolve().parents[1]
        code = (root / "src/minute_ma/integrated_raw_retention.py").read_text()
        for forbidden in ("DELETE FROM", "UPDATE public.", "INSERT INTO", "raw_flow_execution",
                          "raw_flow_program", "raw_flow_orderbook_5s", "live_repository", "KIS POST"):
            self.assertNotIn(forbidden, code)
        self.assertIn("older_than => %s::timestamp, newer_than => %s::timestamp", code)
        self.assertIn("range_start AT TIME ZONE 'UTC'", code)

    def test_independent_timer_no_catchup_or_trading_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / "systemd/trading-minute-ma-integrated-raw-retention.service").read_text()
        timer = (root / "systemd/trading-minute-ma-integrated-raw-retention.timer").read_text()
        self.assertIn("21:30:00 Asia/Seoul", timer)
        self.assertIn("Persistent=false", timer)
        for line in service.splitlines():
            self.assertFalse(line.startswith(("Requires=", "PartOf=", "BindsTo=", "OnFailure=", "Restart=")))

    def test_migration_only_changes_future_interval(self):
        text = (Path(__file__).resolve().parents[1] /
                "database/migrations/20260915_minute_ma_integrated_raw_chunk_interval.sql").read_text()
        body = "\n".join(line for line in text.splitlines() if not line.startswith("--"))
        self.assertIn("INTERVAL '1 day'", body)
        for forbidden in ("DELETE", "UPDATE", "DROP", "INSERT", "drop_chunks", "create_hypertable"):
            self.assertNotIn(forbidden, body)


if __name__ == "__main__":
    unittest.main()
