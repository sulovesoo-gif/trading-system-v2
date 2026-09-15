"""Offline H0UNCNT0 retention. No runtime, broker, or bar write path.

All candidates are verified before drop_chunks; one transaction rolls back every
drop on failure. The default CLI transaction is READ ONLY.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from itertools import groupby
from typing import Callable

from psycopg import sql

from src.flow_raw.realtime_minute import ExecutionTick, build_realtime_minute_bars

RAW = "public.raw_minute_ma_integrated_execution"
BAR = "public.minute_ma_integrated_realtime_minute_bar"
SYMBOLS = ("000660", "005930")
LOCK_ID = 7262091501
AFTER_MARKET = time(21, 30)


class RetentionBlocked(RuntimeError):
    """No chunks may be removed when verification is uncertain."""


@dataclass(frozen=True)
class Chunk:
    schema: str
    name: str
    start: datetime
    end: datetime
    size_bytes: int

    @property
    def qualified(self):
        return f"{self.schema}.{self.name}"


def retention_cutoff(days: dict[str, tuple[date, date]], today: date) -> datetime:
    """Keep each instrument's last two actual data dates, plus all of today.

    On holidays this deliberately retains two previous data days. A lagging
    symbol can only move the global cutoff backwards, never forwards.
    """
    if set(days) != set(SYMBOLS):
        raise RetentionBlocked("DATA_DAYS_MISSING")
    for latest, previous in days.values():
        if not previous < latest <= today:
            raise RetentionBlocked("DATA_DAY_ORDER_INVALID")
    return datetime.combine(min(previous for _, previous in days.values()), time())


def eligible_chunks(chunks: list[Chunk], cutoff: datetime) -> list[Chunk]:
    ordered = sorted(chunks, key=lambda c: c.start)
    for i, chunk in enumerate(ordered):
        if chunk.start >= chunk.end or (i and ordered[i - 1].end > chunk.start):
            raise RetentionBlocked("CHUNK_BOUNDARY_INVALID")
    return [c for c in ordered if c.end <= cutoff]


# Match the immutable builder output, including late-arriving event detection.
MATCH_FIELDS = (
    "open_price", "high_price", "low_price", "close_price", "volume",
    "execution_volume_sum", "first_accumulated_volume", "last_accumulated_volume",
    "event_count", "message_count", "first_source_event_time", "last_source_event_time",
    "first_received_at", "last_received_at", "connection_count", "reconnect_flag",
    "source_gap_flag", "event_time_regression_flag", "ordering_invariant_failure",
    "accumulated_volume_regression", "duplicate_excluded_count",
)


def verify_bar(ticks: list[ExecutionTick], target: datetime, stock: str,
               stored: dict | None, *, now: datetime, grace_ms: int) -> None:
    expected = next((b for b in build_realtime_minute_bars(
        ticks, now=now, grace_ms=grace_ms)
        if b.stock_code == stock and b.bar_time == target), None)
    if expected is None:
        # The existing builder deliberately emits no bar for duplicate-only minutes.
        if any(not t.duplicate_flag for t in ticks if t.minute == target):
            raise RetentionBlocked(f"UNFINALIZED_MINUTE:{stock}:{target}")
        if stored is not None:
            raise RetentionBlocked(f"DUPLICATE_ONLY_BAR_MISMATCH:{stock}:{target}")
        return
    if stored is None:
        raise RetentionBlocked(f"BAR_MISSING:{stock}:{target}")
    for field in MATCH_FIELDS:
        if stored[field] != getattr(expected, field):
            raise RetentionBlocked(f"BAR_MISMATCH:{stock}:{target}:{field}")
    if any(getattr(expected, f) for f in (
        "source_gap_flag", "event_time_regression_flag", "ordering_invariant_failure",
        "accumulated_volume_regression",
    )):
        raise RetentionBlocked(f"RAW_QUALITY_UNSAFE:{stock}:{target}")
    # Do not pretend incomplete bars are COMPLETE. The builder marks the first
    # actual minute (no previous cumulative volume) INCOMPLETE by construction.
    # Only that exactly reproduced boundary is allowed, never unknown reasons.
    reasons = set(stored["quality_reasons"])
    allowed = {"NO_NEXT_MINUTE_EVENT", "RECONNECT_BOUNDARY"}
    if expected.volume is None and "PREVIOUS_MINUTE_ACCUMULATED_VOLUME_MISSING" in expected.quality_reasons:
        allowed.add("PREVIOUS_MINUTE_ACCUMULATED_VOLUME_MISSING")
    if not reasons <= allowed:
        raise RetentionBlocked(f"BAR_QUALITY_UNSAFE:{stock}:{target}")
    expected_reasons = set(expected.quality_reasons) - {"NO_NEXT_MINUTE_EVENT"}
    if reasons - {"NO_NEXT_MINUTE_EVENT"} != expected_reasons:
        raise RetentionBlocked(f"BAR_QUALITY_MISMATCH:{stock}:{target}")
    quality = ("INCOMPLETE" if "PREVIOUS_MINUTE_ACCUMULATED_VOLUME_MISSING" in reasons
               else "SUSPECT" if reasons else "COMPLETE")
    if stored["quality_status"] != quality or stored["raw_source"] != "KIS_H0UNCNT0_INTEGRATED":
        raise RetentionBlocked(f"BAR_CONTRACT_MISMATCH:{stock}:{target}")
    end = target + timedelta(minutes=1)
    final = stored["finalized_at"]
    reason = stored["finalize_reason"]
    if (final < end or final > now or reason not in ("NEXT_MINUTE_EVENT", "GRACE_WATERMARK")
            or (reason == "GRACE_WATERMARK" and final < end + timedelta(milliseconds=grace_ms))
            or ((reason == "GRACE_WATERMARK") != ("NO_NEXT_MINUTE_EVENT" in reasons))):
        raise RetentionBlocked(f"BAR_NOT_FINALIZED:{stock}:{target}")


class PostgresRetentionStore:
    def __init__(self, connection, *, grace_ms: int = 2000):
        self.c = connection
        self.grace_ms = grace_ms

    def check_dimension(self):
        rows = self.c.execute("""SELECT column_name,column_type::text
            FROM timescaledb_information.dimensions
            WHERE hypertable_schema='public'
              AND hypertable_name='raw_minute_ma_integrated_execution'""").fetchall()
        if rows != [("received_at", "timestamp without time zone")]:
            raise RetentionBlocked("UNEXPECTED_HYPERTABLE_DIMENSION")

    def data_days(self, today: date):
        result = {}
        for stock in SYMBOLS:
            upper = datetime.combine(today + timedelta(days=1), time())
            days = []
            for _ in range(2):
                value = self.c.execute(f"""SELECT max(source_event_time) FROM {RAW}
                    WHERE stock_code=%s AND source_event_time < %s""", (stock, upper)).fetchone()[0]
                if value is None:
                    raise RetentionBlocked(f"TWO_DATA_DAYS_REQUIRED:{stock}")
                days.append(value.date())
                upper = datetime.combine(value.date(), time())
            result[stock] = tuple(days)
        return result

    def chunks(self):
        # Timescale exposes timestamptz even for a timestamp dimension. UTC
        # conversion restores the actual naive CHECK bounds, NOT KST wall time.
        return [Chunk(*row) for row in self.c.execute("""SELECT chunk_schema,chunk_name,
            range_start AT TIME ZONE 'UTC',range_end AT TIME ZONE 'UTC',
            pg_total_relation_size(format('%I.%I',chunk_schema,chunk_name)::regclass)
            FROM timescaledb_information.chunks
            WHERE hypertable_schema='public' AND hypertable_name='raw_minute_ma_integrated_execution'
            ORDER BY range_start""").fetchall()]

    def acquire(self):
        if not self.c.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_ID,)).fetchone()[0]:
            raise RetentionBlocked("MAINTENANCE_ALREADY_RUNNING")

    def lock_candidates(self, candidates):
        for chunk in candidates:
            # Only cold chunks; never lock the parent or current collector chunk.
            self.c.execute(sql.SQL("LOCK TABLE ONLY {}.{} IN SHARE MODE").format(
                sql.Identifier(chunk.schema), sql.Identifier(chunk.name)))

    def verify_candidates(self, candidates, cutoff, now):
        affected = set()
        for chunk in candidates:
            relation = sql.Identifier(chunk.schema, chunk.name)
            bad = self.c.execute(sql.SQL("""SELECT count(*) FROM {} WHERE
                business_date >= %s OR source_event_time >= %s OR received_at >= %s
                OR business_date <> source_event_time::date
                OR business_date <> received_at::date
                OR stock_code NOT IN ('000660','005930')
                OR trading_venue <> 'INTEGRATED' OR tr_id <> 'H0UNCNT0'""").format(relation),
                (cutoff.date(), cutoff, cutoff)).fetchone()[0]
            if bad:
                raise RetentionBlocked(f"PROTECTED_OR_CROSS_DAY_RAW:{chunk.qualified}:{bad}")
            affected.update(self.c.execute(sql.SQL(
                "SELECT DISTINCT stock_code,business_date FROM {}"
            ).format(relation)).fetchall())
        count = 0
        for stock, day in sorted(affected):
            count += self.verify_day(stock, day, now)
        return count

    def verify_day(self, stock, day, now):
        start = datetime.combine(day, time())
        end = start + timedelta(days=1)
        columns = ("bar_time", *MATCH_FIELDS, "finalized_at", "finalize_reason", "quality_status",
                   "quality_reasons", "raw_source")
        rows = self.c.execute(f"SELECT {','.join(columns)} FROM {BAR} "
            "WHERE stock_code=%s AND trading_venue='INTEGRATED' AND bar_time >= %s AND bar_time < %s",
            (stock, start, end)).fetchall()
        bars = {r[0]: dict(zip(columns, r)) for r in rows}
        query = f"""SELECT e.stock_code,e.source_event_time,c.connected_at,e.receive_sequence,
            e.event_index,e.received_at,e.current_price,e.execution_volume,e.accumulated_volume,
            e.connection_id::text,e.reconnect_flag,e.source_gap_flag,e.event_time_regression_flag,e.duplicate_flag
            FROM {RAW} e LEFT JOIN public.minute_ma_integrated_ws_connection c USING(connection_id)
            WHERE e.stock_code=%s AND e.source_event_time >= %s AND e.source_event_time < %s
            ORDER BY e.source_event_time,c.connected_at,e.receive_sequence,e.event_index"""
        count = 0
        # Stream at most three source minutes, not multi-million-row days into RAM.
        with self.c.cursor(name="integrated_retention_ticks") as cur:
            cur.itersize = 2000
            cur.execute(query, (stock, start - timedelta(minutes=1), end + timedelta(minutes=1)))

            def ticks():
                for row in cur:
                    if row[2] is None:
                        raise RetentionBlocked("RAW_CONNECTION_MISSING")
                    yield ExecutionTick(*row)

            groups = ((minute, list(group)) for minute, group in groupby(ticks(), key=lambda t: t.minute))
            previous = []
            current = next(groups, None)
            while current:
                following = next(groups, None)
                minute, items = current
                if start <= minute < end:
                    verify_bar(previous + items + (following[1] if following else []), minute, stock,
                               bars.get(minute), now=now, grace_ms=self.grace_ms)
                    count += 1
                previous = items
                current = following
        if count == 0:
            raise RetentionBlocked(f"RAW_DISAPPEARED_DURING_VERIFICATION:{stock}:{day}")
        return count

    def drop(self, chunk):
        # Whole exact time window only. No broad older_than-only sweep.
        return [r[0] for r in self.c.execute("""SELECT drop_chunks(%s::regclass,
            older_than => %s::timestamp, newer_than => %s::timestamp)::text""",
            (RAW, chunk.end, chunk.start)).fetchall()]


def run_retention(store, *, now: datetime, apply: bool, emit: Callable = lambda event: None):
    """Caller MUST own a transaction and commit only a successful result."""
    if now.tzinfo is not None:
        raise RetentionBlocked("EXPECTED_NAIVE_KST_CLOCK")
    if now.time() < AFTER_MARKET:
        raise RetentionBlocked("OUTSIDE_AFTER_MARKET_WINDOW_21_30_TO_24_KST")
    store.check_dimension()
    days = store.data_days(now.date())
    cutoff = retention_cutoff(days, now.date())
    chunks = store.chunks()
    candidates = eligible_chunks(chunks, cutoff)
    result = {"run_at_kst": str(now), "data_days_by_stock": days, "retention_cutoff": cutoff,
              "candidate_chunks": [asdict(c) for c in candidates],
              "candidate_bytes": sum(c.size_bytes for c in candidates),
              "hypertable_chunk_bytes_before": sum(c.size_bytes for c in chunks),
              "actual_removed_chunks": [], "verified_minutes": 0}
    emit({"status": "PLAN", **result})
    if not candidates:
        return {**result, "status": "SKIP", "reason": "NO_COMPLETE_OLD_CHUNKS"}
    if apply:
        store.acquire()
        store.lock_candidates(candidates)
        # Chunk topology must stay unchanged; sizes can legitimately grow.
        fresh = eligible_chunks(store.chunks(), cutoff)
        if [(c.qualified, c.start, c.end) for c in fresh] != [(c.qualified, c.start, c.end) for c in candidates]:
            raise RetentionBlocked("CANDIDATE_SET_CHANGED")
    result["verified_minutes"] = store.verify_candidates(candidates, cutoff, now)
    if not apply:
        return {**result, "status": "DRY_RUN_PASS", "reason": "VERIFIED_NO_DELETION"}
    for chunk in candidates:
        removed = store.drop(chunk)
        if removed != [chunk.qualified]:
            raise RetentionBlocked(f"UNEXPECTED_DROP_RESULT:{removed}")
        result["actual_removed_chunks"].extend(removed)
    result["hypertable_chunk_bytes_after"] = sum(c.size_bytes for c in store.chunks())
    return {**result, "status": "COMPLETE", "reason": "VERIFIED_CHUNKS_ONLY"}
