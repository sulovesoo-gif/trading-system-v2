"""Persistence for SMA signals, notification history, and restart-safe ARMED state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class SmaCrossSignal:
    signal_id: int
    signal_time: datetime
    stock_code: str
    direction: str
    status: str
    signal_price: Decimal


@dataclass(frozen=True)
class ArmedState:
    stock_code: str
    armed_direction: str
    ma_cross_time: datetime
    ma_cross_price: Decimal
    ma_cross_sma5: Decimal
    ma_cross_sma10: Decimal
    candidate_signal_id: int | None


class SmaCrossSignalRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def latest_confirmed(self, stock_code: str) -> SmaCrossSignal | None:
        return self._signal_one(
            "SELECT signal_id, COALESCE(confirmed_time, signal_time), stock_code, direction, status, "
            "COALESCE(confirmed_price, signal_price) "
            "FROM analysis_sma_cross_signal WHERE stock_code = %s "
            "AND status IN ('INITIAL_CONFIRMED', 'CONFIRMED') "
            "ORDER BY COALESCE(confirmed_time, signal_time) DESC, signal_id DESC LIMIT 1",
            (stock_code,),
        )

    def active_candidate(self, stock_code: str) -> SmaCrossSignal | None:
        return self._signal_one(
            "SELECT signal_id, signal_time, stock_code, direction, status, signal_price "
            "FROM analysis_sma_cross_signal WHERE stock_code = %s AND status = 'CANDIDATE' "
            "ORDER BY signal_time DESC, signal_id DESC LIMIT 1",
            (stock_code,),
        )

    def armed_state(self, stock_code: str) -> ArmedState | None:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT stock_code, armed_direction, ma_cross_time, ma_cross_price, ma_cross_sma5, ma_cross_sma10, candidate_signal_id "
                    "FROM analysis_sma_cross_arm_state WHERE stock_code = %s",
                    (stock_code,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return ArmedState(str(row[0]), str(row[1]), row[2], Decimal(row[3]), Decimal(row[4]), Decimal(row[5]), row[6])

    def upsert_arm(self, *, stock_code: str, armed_direction: str, ma_cross_time: datetime,
                   ma_cross_price: Decimal, ma_cross_sma5: Decimal, ma_cross_sma10: Decimal,
                   preserve_candidate: bool = False) -> ArmedState:
        sql = (
            "INSERT INTO analysis_sma_cross_arm_state "
            "(stock_code, armed_direction, ma_cross_time, ma_cross_price, ma_cross_sma5, ma_cross_sma10) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (stock_code) DO UPDATE SET armed_direction = EXCLUDED.armed_direction, "
            "ma_cross_time = EXCLUDED.ma_cross_time, ma_cross_price = EXCLUDED.ma_cross_price, "
            "ma_cross_sma5 = EXCLUDED.ma_cross_sma5, ma_cross_sma10 = EXCLUDED.ma_cross_sma10, "
            "candidate_signal_id = CASE WHEN %s THEN analysis_sma_cross_arm_state.candidate_signal_id ELSE NULL END, "
            "updated_at = CURRENT_TIMESTAMP "
            "RETURNING stock_code, armed_direction, ma_cross_time, ma_cross_price, ma_cross_sma5, ma_cross_sma10, candidate_signal_id"
        )
        with self.pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(sql, (stock_code, armed_direction, ma_cross_time, ma_cross_price, ma_cross_sma5, ma_cross_sma10, preserve_candidate))
                    row = cursor.fetchone()
        return ArmedState(str(row[0]), str(row[1]), row[2], Decimal(row[3]), Decimal(row[4]), Decimal(row[5]), row[6])

    def set_arm_candidate(self, *, stock_code: str, signal_id: int) -> None:
        self._execute(
            "UPDATE analysis_sma_cross_arm_state SET candidate_signal_id = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE stock_code = %s", (signal_id, stock_code)
        )

    def clear_arm(self, stock_code: str) -> None:
        self._execute("DELETE FROM analysis_sma_cross_arm_state WHERE stock_code = %s", (stock_code,))

    def signal_exists_at(self, *, stock_code: str, signal_time: datetime) -> bool:
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM analysis_sma_cross_signal WHERE stock_code = %s AND signal_time = %s)",
                    (stock_code, signal_time),
                )
                return bool(cursor.fetchone()[0])

    def create(self, values: dict[str, object]) -> SmaCrossSignal:
        columns = tuple(values)
        sql = (
            "INSERT INTO analysis_sma_cross_signal (" + ", ".join(columns) + ") VALUES ("
            + ", ".join("%s" for _ in columns) + ") "
            "RETURNING signal_id, signal_time, stock_code, direction, status, signal_price"
        )
        return self._signal_one(sql, tuple(values[name] for name in columns), required=True)

    def confirm_candidate(self, *, signal_id: int, confirmed_time: datetime, confirmed_price: Decimal,
                          range_metrics: dict[str, object]) -> SmaCrossSignal:
        return self._signal_one(
            "UPDATE analysis_sma_cross_signal SET status = 'CONFIRMED', volatility_threshold_met = TRUE, "
            "confirmed_time = %s, confirmed_price = %s, highest_close_since_previous = %s, highest_close_time = %s, "
            "lowest_close_since_previous = %s, lowest_close_time = %s, close_range_return = %s, "
            "status_updated_at = CURRENT_TIMESTAMP WHERE signal_id = %s AND status = 'CANDIDATE' "
            "RETURNING signal_id, confirmed_time, stock_code, direction, status, confirmed_price",
            (confirmed_time, confirmed_price, range_metrics['highest_close_since_previous'], range_metrics['highest_close_time'],
             range_metrics['lowest_close_since_previous'], range_metrics['lowest_close_time'],
             range_metrics['close_range_return'], signal_id), required=True,
        )

    def reject_candidate(self, *, signal_id: int, reason: str) -> None:
        self._execute(
            "UPDATE analysis_sma_cross_signal SET status = 'REJECTED', rejection_reason = %s, status_updated_at = CURRENT_TIMESTAMP "
            "WHERE signal_id = %s AND status = 'CANDIDATE'", (reason, signal_id)
        )

    def create_notification(self, *, signal_id: int, notification_type: str) -> bool:
        return self._execute(
            "INSERT INTO analysis_signal_notification (signal_id, notification_type, delivery_status) "
            "VALUES (%s, %s, 'PENDING') ON CONFLICT (signal_id, notification_type) DO NOTHING RETURNING notification_id",
            (signal_id, notification_type), returning=True,
        ) is not None

    def signal_details(self, signal_id: int) -> dict[str, object]:
        sql = (
            "SELECT signal_time, signal_price, confirmed_time, confirmed_price, previous_confirmed_signal_price, "
            "armed_direction, ma_cross_time, ma_cross_price, armed_wait_minutes, highest_close_since_previous, "
            "highest_close_time, lowest_close_since_previous, lowest_close_time, close_range_return, volatility_threshold_met, "
            "direction_alignment FROM analysis_sma_cross_signal WHERE signal_id = %s"
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (signal_id,))
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("SMA signal was not found for notification.")
        names = (
            "signal_time", "signal_price", "confirmed_time", "confirmed_price", "previous_price", "armed_direction",
            "ma_cross_time", "ma_cross_price", "armed_wait_minutes", "highest_close", "highest_close_time",
            "lowest_close", "lowest_close_time", "close_range_return", "threshold_met", "alignment",
        )
        return dict(zip(names, row))

    def mark_notification_sent(self, *, signal_id: int, notification_type: str) -> None:
        self._execute(
            "UPDATE analysis_signal_notification SET delivery_status = 'SENT', attempt_count = attempt_count + 1, "
            "sent_at = CURRENT_TIMESTAMP, failure_message = NULL WHERE signal_id = %s AND notification_type = %s",
            (signal_id, notification_type),
        )

    def mark_notification_failed(self, *, signal_id: int, notification_type: str, message: str) -> None:
        self._execute(
            "UPDATE analysis_signal_notification SET delivery_status = 'FAILED', attempt_count = attempt_count + 1, failure_message = %s "
            "WHERE signal_id = %s AND notification_type = %s", (message[:500], signal_id, notification_type)
        )

    def ensure_performance(self, signal_id: int) -> None:
        self._execute("INSERT INTO analysis_sma_cross_performance (signal_id) VALUES (%s) ON CONFLICT DO NOTHING", (signal_id,))

    def save_related_bar(self, *, signal_id: int, stock_code: str, bar) -> None:
        self._execute(
            "INSERT INTO analysis_sma_cross_related_bar "
            "(signal_id, stock_code, trading_venue, bar_time, open_price, high_price, low_price, close_price, volume, accumulated_amount) "
            "VALUES (%s, %s, 'KRX', %s, %s, %s, %s, %s, NULL, NULL) ON CONFLICT DO NOTHING",
            (signal_id, stock_code, bar.bar_time, bar.open_price, bar.high_price, bar.low_price, bar.close_price),
        )

    def update_performance(self, *, signal_id: int, returns: dict[str, Decimal | datetime | None], end_reason: str | None = None) -> None:
        assignments, values = [], []
        for column, value in returns.items():
            if column == "maximum_up_return_until_next_confirmed":
                assignments.append(f"{column} = GREATEST(COALESCE({column}, %s), %s)")
                values.extend((value, value))
            elif column == "maximum_down_return_until_next_confirmed":
                assignments.append(f"{column} = LEAST(COALESCE({column}, %s), %s)")
                values.extend((value, value))
            else:
                assignments.append(f"{column} = %s")
                values.append(value)
        if end_reason:
            assignments.extend(("performance_end_reason = %s", "performance_end_time = %s"))
            values.extend((end_reason, returns["last_evaluated_bar_time"]))
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        self._execute("UPDATE analysis_sma_cross_performance SET " + ", ".join(assignments) + " WHERE signal_id = %s", tuple(values + [signal_id]))

    def _signal_one(self, sql: str, values: tuple[object, ...], required: bool = False) -> SmaCrossSignal | None:
        with self.pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(sql, values)
                    row = cursor.fetchone()
        if row is None:
            if required:
                raise RuntimeError("Required SMA signal query returned no row.")
            return None
        return SmaCrossSignal(int(row[0]), row[1], str(row[2]), str(row[3]), str(row[4]), Decimal(row[5]))

    def _execute(self, sql: str, values: tuple[object, ...], returning: bool = False):
        with self.pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(sql, values)
                    return cursor.fetchone() if returning else None
