"""Deterministic H0STCNT0 -> immutable research 1MIN bars.

These bars are comparison evidence only.  Minute MA runtimes continue to use
their existing approved source until a separate source-switch approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class ExecutionTick:
    stock_code: str
    source_event_time: datetime
    connection_connected_at: datetime
    receive_sequence: int
    event_index: int
    received_at: datetime
    current_price: int
    execution_volume: int | None
    accumulated_volume: int | None
    connection_id: str
    reconnect_flag: bool = False
    source_gap_flag: bool = False
    event_time_regression_flag: bool = False
    duplicate_flag: bool = False

    @property
    def minute(self) -> datetime:
        return self.source_event_time.replace(second=0, microsecond=0)

    @property
    def order_key(self) -> tuple:
        return (
            self.source_event_time,
            self.connection_connected_at,
            self.receive_sequence,
            self.event_index,
        )


@dataclass(frozen=True)
class RealtimeMinuteBar:
    bar_time: datetime
    stock_code: str
    open_price: int
    high_price: int
    low_price: int
    close_price: int
    volume: int | None
    execution_volume_sum: int
    first_accumulated_volume: int | None
    last_accumulated_volume: int | None
    event_count: int
    message_count: int
    first_source_event_time: datetime
    last_source_event_time: datetime
    first_received_at: datetime
    last_received_at: datetime
    finalized_at: datetime
    finalize_reason: str
    watermark_delay_ms: float
    connection_count: int
    reconnect_flag: bool
    source_gap_flag: bool
    event_time_regression_flag: bool
    ordering_invariant_failure: bool
    accumulated_volume_regression: bool
    duplicate_excluded_count: int
    quality_status: str
    quality_reasons: tuple[str, ...]


def build_realtime_minute_bars(
    ticks: list[ExecutionTick] | tuple[ExecutionTick, ...],
    *,
    now: datetime,
    grace_ms: int = 2000,
) -> tuple[RealtimeMinuteBar, ...]:
    """Build only finalized bars; a minute with no tick is never invented."""
    by_symbol: dict[str, list[ExecutionTick]] = {}
    for tick in ticks:
        by_symbol.setdefault(tick.stock_code, []).append(tick)

    result: list[RealtimeMinuteBar] = []
    grace = timedelta(milliseconds=grace_ms)
    for stock_code, symbol_ticks in by_symbol.items():
        ordered_all = sorted(symbol_ticks, key=lambda item: item.order_key)
        groups: dict[datetime, list[ExecutionTick]] = {}
        for item in ordered_all:
            groups.setdefault(item.minute, []).append(item)
        minutes = list(groups)
        for index, minute in enumerate(minutes):
            minute_end = minute + timedelta(minutes=1)
            later = groups[minutes[index + 1]][0] if index + 1 < len(minutes) else None
            if later is not None:
                finalized_at = later.received_at
                reason = "NEXT_MINUTE_EVENT"
            elif now >= minute_end + grace:
                finalized_at = now
                reason = "GRACE_WATERMARK"
            else:
                continue

            raw_group = groups[minute]
            valid = [item for item in raw_group if not item.duplicate_flag]
            if not valid:
                continue
            valid.sort(key=lambda item: item.order_key)
            prices = [item.current_price for item in valid]
            accumulated = [item.accumulated_volume for item in valid if item.accumulated_volume is not None]
            previous_last = None
            if index > 0 and minutes[index - 1] == minute - timedelta(minutes=1):
                previous_valid = [item for item in groups[minutes[index - 1]] if not item.duplicate_flag]
                previous_valid.sort(key=lambda item: item.order_key)
                previous_accumulated = [item.accumulated_volume for item in previous_valid if item.accumulated_volume is not None]
                previous_last = previous_accumulated[-1] if previous_accumulated else None

            last_accumulated = accumulated[-1] if accumulated else None
            volume = None if previous_last is None or last_accumulated is None else last_accumulated - previous_last
            accumulated_regression = volume is not None and volume < 0
            ordering_failure = len({item.order_key for item in valid}) != len(valid)
            reasons: list[str] = []
            if previous_last is None:
                reasons.append("PREVIOUS_MINUTE_ACCUMULATED_VOLUME_MISSING")
            if accumulated_regression:
                reasons.append("ACCUMULATED_VOLUME_REGRESSION")
                volume = None
            if any(item.source_gap_flag for item in valid):
                reasons.append("SOURCE_GAP")
            if any(item.event_time_regression_flag for item in valid):
                reasons.append("EVENT_TIME_REGRESSION")
            if ordering_failure:
                reasons.append("ORDERING_INVARIANT_FAILURE")
            if any(item.reconnect_flag for item in valid):
                reasons.append("RECONNECT_BOUNDARY")
            if reason == "GRACE_WATERMARK":
                reasons.append("NO_NEXT_MINUTE_EVENT")
            quality = "COMPLETE" if not reasons else (
                "INCOMPLETE" if any(reason in reasons for reason in (
                    "PREVIOUS_MINUTE_ACCUMULATED_VOLUME_MISSING", "ACCUMULATED_VOLUME_REGRESSION", "SOURCE_GAP",
                    "ORDERING_INVARIANT_FAILURE"
                )) else "SUSPECT"
            )
            result.append(RealtimeMinuteBar(
                bar_time=minute,
                stock_code=stock_code,
                open_price=prices[0], high_price=max(prices), low_price=min(prices), close_price=prices[-1],
                volume=volume,
                execution_volume_sum=sum(abs(item.execution_volume or 0) for item in valid),
                first_accumulated_volume=accumulated[0] if accumulated else None,
                last_accumulated_volume=last_accumulated,
                event_count=len(valid),
                message_count=len({(item.connection_id, item.receive_sequence) for item in valid}),
                first_source_event_time=valid[0].source_event_time,
                last_source_event_time=valid[-1].source_event_time,
                first_received_at=min(item.received_at for item in valid),
                last_received_at=max(item.received_at for item in valid),
                finalized_at=finalized_at,
                finalize_reason=reason,
                watermark_delay_ms=max(0.0, (finalized_at - minute_end).total_seconds() * 1000),
                connection_count=len({item.connection_id for item in valid}),
                reconnect_flag=any(item.reconnect_flag for item in valid),
                source_gap_flag=any(item.source_gap_flag for item in valid),
                event_time_regression_flag=any(item.event_time_regression_flag for item in valid),
                ordering_invariant_failure=ordering_failure,
                accumulated_volume_regression=accumulated_regression,
                duplicate_excluded_count=len(raw_group) - len(valid),
                quality_status=quality,
                quality_reasons=tuple(dict.fromkeys(reasons)),
            ))
    return tuple(sorted(result, key=lambda item: (item.bar_time, item.stock_code)))


def compare_rest(bar: RealtimeMinuteBar, rest_bar) -> tuple[str, tuple[str, ...]]:
    fields = {
        "open": bar.open_price == int(rest_bar.open_price),
        "high": bar.high_price == int(rest_bar.high_price),
        "low": bar.low_price == int(rest_bar.low_price),
        "close": bar.close_price == int(rest_bar.close_price),
        "volume": bar.volume is not None and bar.volume == int(rest_bar.volume),
    }
    mismatches = tuple(name for name, matches in fields.items() if not matches)
    return ("MATCH" if not mismatches else "MISMATCH", mismatches)
