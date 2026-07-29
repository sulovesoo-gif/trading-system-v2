"""백필 운영 메타데이터 저장소.

RAW Repository와 분리하여 백필 실행 상태와 페이지 커서만 기록한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class BackfillSegment:
    segment_id: int
    job_id: int
    instrument_code: str
    trading_venue: str
    trade_date: date
    page_sequence: int
    cursor_date: str | None = None
    cursor_time: str | None = None
    continuation_code: str | None = None


class BackfillRepository:
    """Uses fixed SQL statements for `backfill_job` and `backfill_segment` only."""

    def __init__(self, pool) -> None:
        self.pool = pool

    def create_job(self, *, job_type: str, start_date: date, end_date: date) -> int:
        sql = (
            "INSERT INTO backfill_job (job_type, start_date, end_date) "
            "VALUES (%s, %s, %s) RETURNING job_id"
        )
        with self.pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(sql, (job_type, start_date, end_date))
                    return int(cursor.fetchone()[0])

    def mark_job_running(self, job_id: int) -> None:
        self._execute(
            "UPDATE backfill_job SET status = 'RUNNING', started_at = COALESCE(started_at, CURRENT_TIMESTAMP), "
            "failure_message = NULL WHERE job_id = %s",
            (job_id,),
        )

    def mark_job_completed(self, job_id: int) -> None:
        self._execute(
            "UPDATE backfill_job SET status = 'COMPLETED', completed_at = CURRENT_TIMESTAMP WHERE job_id = %s",
            (job_id,),
        )

    def mark_job_failed(self, job_id: int, message: str) -> None:
        self._execute(
            "UPDATE backfill_job SET status = 'FAILED', failure_message = %s, completed_at = CURRENT_TIMESTAMP WHERE job_id = %s",
            (message[:1000], job_id),
        )

    def stock_backfill_targets(self) -> list[tuple[str, str]]:
        sql = (
            "SELECT stock_code, market FROM stock_master "
            "WHERE collect_yn = 'Y' AND use_yn = 'Y' "
            "ORDER BY sort_order, stock_code"
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                return [(str(code), str(market)) for code, market in cursor.fetchall()]

    def create_segment(
        self,
        *,
        job_id: int,
        instrument_code: str,
        trading_venue: str,
        trade_date: date,
        page_sequence: int = 1,
        cursor_date: str | None = None,
        cursor_time: str | None = None,
        continuation_code: str | None = None,
    ) -> BackfillSegment:
        sql = (
            "INSERT INTO backfill_segment "
            "(job_id, instrument_code, trading_venue, trade_date, page_sequence, cursor_date, cursor_time, continuation_code) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (job_id, instrument_code, trading_venue, trade_date, page_sequence) "
            "DO UPDATE SET segment_id = backfill_segment.segment_id "
            "RETURNING segment_id, cursor_date, cursor_time, continuation_code"
        )
        values = (job_id, instrument_code, trading_venue, trade_date, page_sequence, cursor_date, cursor_time, continuation_code)
        with self.pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(sql, values)
                    row = cursor.fetchone()
        return BackfillSegment(int(row[0]), job_id, instrument_code, trading_venue, trade_date, page_sequence, row[1], row[2], row[3])

    def resumable_segments(self, job_id: int) -> list[BackfillSegment]:
        sql = (
            "SELECT segment_id, job_id, instrument_code, trading_venue, trade_date, page_sequence, "
            "cursor_date, cursor_time, continuation_code "
            "FROM backfill_segment WHERE job_id = %s AND status IN ('PENDING', 'RUNNING', 'FAILED') "
            "ORDER BY trade_date, instrument_code, page_sequence"
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (job_id,))
                return [BackfillSegment(*row) for row in cursor.fetchall()]

    def completed_summary(self, job_id: int) -> list[tuple]:
        sql = (
            "SELECT instrument_code, count(*) AS segments, sum(request_count), sum(returned_count), "
            "sum(inserted_count), sum(duplicate_count), min(minimum_bar_time), max(maximum_bar_time), "
            "count(*) FILTER (WHERE status = 'FAILED') "
            "FROM backfill_segment WHERE job_id = %s "
            "GROUP BY instrument_code ORDER BY instrument_code"
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (job_id,))
                return cursor.fetchall()

    def mark_running(self, segment_id: int) -> None:
        self._execute(
            "UPDATE backfill_segment SET status = 'RUNNING', attempt_count = attempt_count + 1, "
            "failure_message = NULL, completed_at = NULL, "
            "started_at = COALESCE(started_at, CURRENT_TIMESTAMP) WHERE segment_id = %s",
            (segment_id,),
        )

    def record_page(
        self,
        *,
        segment_id: int,
        continuation_code: str | None,
        cursor_date: str | None,
        cursor_time: str | None,
        returned_count: int,
        inserted_count: int,
        duplicate_count: int,
        minimum_bar_time: datetime | None,
        maximum_bar_time: datetime | None,
    ) -> None:
        self._execute(
            "UPDATE backfill_segment SET request_count = request_count + 1, "
            "returned_count = returned_count + %s, inserted_count = inserted_count + %s, "
            "duplicate_count = duplicate_count + %s, continuation_code = %s, cursor_date = %s, cursor_time = %s, "
            "minimum_bar_time = COALESCE(LEAST(minimum_bar_time, %s), %s), "
            "maximum_bar_time = COALESCE(GREATEST(maximum_bar_time, %s), %s) WHERE segment_id = %s",
            (
                returned_count, inserted_count, duplicate_count, continuation_code, cursor_date, cursor_time,
                minimum_bar_time, minimum_bar_time, maximum_bar_time, maximum_bar_time, segment_id,
            ),
        )

    def mark_completed(self, segment_id: int) -> None:
        self._execute(
            "UPDATE backfill_segment SET status = 'COMPLETED', completed_at = CURRENT_TIMESTAMP WHERE segment_id = %s",
            (segment_id,),
        )

    def mark_failed(self, segment_id: int, message: str) -> None:
        self._execute(
            "UPDATE backfill_segment SET status = 'FAILED', failure_message = %s, completed_at = CURRENT_TIMESTAMP WHERE segment_id = %s",
            (message[:1000], segment_id),
        )

    def _execute(self, sql: str, values: tuple[object, ...]) -> None:
        with self.pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(sql, values)
