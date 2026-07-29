"""SMA 크로스 신호·성과·알림 이력 저장소."""

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


class SmaCrossSignalRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def latest_confirmed(self, stock_code: str) -> SmaCrossSignal | None:
        return self._one(
            "SELECT signal_id, signal_time, stock_code, direction, status, signal_price "
            "FROM analysis_sma_cross_signal WHERE stock_code = %s "
            "AND status IN ('INITIAL_CONFIRMED', 'CONFIRMED') ORDER BY signal_time DESC, signal_id DESC LIMIT 1",
            (stock_code,),
        )

    def active_candidate(self, stock_code: str) -> SmaCrossSignal | None:
        return self._one(
            "SELECT signal_id, signal_time, stock_code, direction, status, signal_price "
            "FROM analysis_sma_cross_signal WHERE stock_code = %s AND status = 'CANDIDATE' "
            "ORDER BY signal_time DESC, signal_id DESC LIMIT 1",
            (stock_code,),
        )

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
        return self._one(sql, tuple(values[name] for name in columns), required=True)

    def confirm_candidate(self, *, signal_id: int, threshold_break_direction: str, threshold_direction_alignment: str) -> SmaCrossSignal:
        return self._one(
            "UPDATE analysis_sma_cross_signal SET status = 'CONFIRMED', threshold_break_direction = %s, "
            "threshold_direction_alignment = %s, volatility_threshold_met = TRUE, status_updated_at = CURRENT_TIMESTAMP "
            "WHERE signal_id = %s AND status = 'CANDIDATE' "
            "RETURNING signal_id, signal_time, stock_code, direction, status, signal_price",
            (threshold_break_direction, threshold_direction_alignment, signal_id), required=True,
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
            "SELECT sma5, sma10, previous_confirmed_signal_price, maximum_up_change_since_previous, "
            "maximum_down_change_since_previous, maximum_absolute_change_since_previous, volatility_threshold_met, "
            "direction_alignment, threshold_break_direction, threshold_direction_alignment "
            "FROM analysis_sma_cross_signal WHERE signal_id = %s"
        )
        with self.pool.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (signal_id,))
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("알림 대상 SMA 신호를 찾을 수 없습니다.")
        names = ("sma5", "sma10", "previous_price", "maximum_up", "maximum_down", "maximum_absolute", "threshold_met", "alignment", "threshold_direction", "threshold_alignment")
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

    def _one(self, sql: str, values: tuple[object, ...], required: bool = False) -> SmaCrossSignal | None:
        with self.pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(sql, values)
                    row = cursor.fetchone()
        if row is None:
            if required:
                raise RuntimeError("SMA 크로스 신호 저장 결과를 찾을 수 없습니다.")
            return None
        return SmaCrossSignal(int(row[0]), row[1], str(row[2]), str(row[3]), str(row[4]), Decimal(row[5]))

    def _execute(self, sql: str, values: tuple[object, ...], returning: bool = False):
        with self.pool.connection() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(sql, values)
                    return cursor.fetchone() if returning else None
