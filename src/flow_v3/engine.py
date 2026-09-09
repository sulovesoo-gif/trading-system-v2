"""Deterministic FLOW V3 calculations copied from the approved replay SQL.

The engine has no database or broker dependency.  FLOW/Velocity windows reset
at each KRX business-date boundary and require contiguous completed minutes.
"""

from __future__ import annotations

from datetime import time, timedelta
from decimal import Decimal
from typing import Iterable, Sequence

from .models import EntrySignal, MinuteBase, MinuteState, StrategyContract

PERIODS = (1, 2, 3, 5, 10, 20, 30)
PAIR_CODE = {
    (1, 3): "01",
    (1, 5): "02",
    (2, 5): "03",
    (3, 5): "04",
    (3, 10): "05",
    (5, 10): "06",
    (5, 20): "07",
    (10, 20): "08",
    (10, 30): "09",
    (20, 30): "10",
}


def _average(values: Sequence[Decimal | None], size: int) -> Decimal | None:
    if len(values) < size:
        return None
    tail = values[-size:]
    if any(value is None for value in tail):
        return None
    return sum((value for value in tail if value is not None), Decimal(0)) / size


def _cross(previous_fast, previous_slow, fast, slow) -> int | None:
    if None in (previous_fast, previous_slow, fast, slow):
        return None
    if previous_fast <= previous_slow and fast > slow:
        return 1
    if previous_fast >= previous_slow and fast < slow:
        return -1
    return None


class FlowV3SignalEngine:
    """Create one completed-minute state and match the 9,600 master rows."""

    def build_state(
        self,
        *,
        base: MinuteBase,
        history: Sequence[MinuteState],
    ) -> MinuteState:
        same_day = [row for row in history if row.business_date == base.business_date]
        same_day.sort(key=lambda row: row.bar_time)
        previous = same_day[-1] if same_day else None
        contiguous_previous = (
            previous is not None
            and previous.is_complete
            and not base.execution_source_gap
            and previous.bar_time == base.bar_time - timedelta(minutes=1)
        )
        velocity = base.flow_value - previous.flow_value if contiguous_previous else None
        program_velocity = None
        if (
            contiguous_previous
            and base.program_net_flow is not None
            and previous.program_net_flow is not None
        ):
            program_velocity = base.program_net_flow - previous.program_net_flow

        flow_values = [row.flow_value for row in same_day] + [base.flow_value]
        velocity_values = [row.velocity_value for row in same_day] + [velocity]
        times = [row.bar_time for row in same_day] + [base.bar_time]
        quality = [row.is_complete for row in same_day] + [not base.execution_source_gap]
        flow_averages: dict[int, Decimal | None] = {}
        velocity_averages: dict[int, Decimal | None] = {}
        for period in PERIODS:
            contiguous = len(times) >= period and all(
                times[-offset] == base.bar_time - timedelta(minutes=offset - 1)
                and quality[-offset]
                for offset in range(1, period + 1)
            )
            flow_averages[period] = _average(flow_values, period) if contiguous else None
            velocity_averages[period] = _average(velocity_values, period) if contiguous else None

        flow_crosses: dict[str, int | None] = {}
        velocity_crosses: dict[str, int | None] = {}
        for pair, pair_code in PAIR_CODE.items():
            fast, slow = pair
            flow_crosses[pair_code] = _cross(
                previous.flow_averages.get(fast) if contiguous_previous else None,
                previous.flow_averages.get(slow) if contiguous_previous else None,
                flow_averages[fast],
                flow_averages[slow],
            )
            velocity_crosses[pair_code] = _cross(
                previous.velocity_averages.get(fast) if contiguous_previous else None,
                previous.velocity_averages.get(slow) if contiguous_previous else None,
                velocity_averages[fast],
                velocity_averages[slow],
            )

        quality_code = "EXECUTION_SOURCE_GAP" if base.execution_source_gap else "OK"
        return MinuteState(
            business_date=base.business_date,
            stock_code=base.stock_code,
            bar_time=base.bar_time,
            underlying_close=base.underlying_close,
            aggressive_buy_amount=base.aggressive_buy_amount,
            aggressive_sell_amount=base.aggressive_sell_amount,
            flow_value=base.flow_value,
            velocity_value=velocity,
            program_net_flow=base.program_net_flow,
            program_velocity=program_velocity,
            long_absorption=(
                base.aggressive_sell_amount > 0
                and base.bid_start is not None
                and base.bid_end is not None
                and base.bid_end >= base.bid_start
                and not base.orderbook_source_gap
            ),
            short_absorption=(
                base.aggressive_buy_amount > 0
                and base.ask_start is not None
                and base.ask_end is not None
                and base.ask_end >= base.ask_start
                and not base.orderbook_source_gap
            ),
            snapshot_count=base.snapshot_count,
            execution_source_gap=base.execution_source_gap,
            execution_duplicate_rows=base.execution_duplicate_rows,
            program_source_gap=base.program_source_gap,
            program_duplicate_rows=base.program_duplicate_rows,
            orderbook_source_gap=base.orderbook_source_gap,
            orderbook_duplicate_rows=base.orderbook_duplicate_rows,
            quality_code=quality_code,
            flow_averages=flow_averages,
            velocity_averages=velocity_averages,
            flow_crosses=flow_crosses,
            velocity_crosses=velocity_crosses,
        )

    def entry_signals(
        self,
        *,
        state: MinuteState,
        recent_states: Sequence[MinuteState],
        strategies: Iterable[StrategyContract],
    ) -> tuple[EntrySignal, ...]:
        if not state.is_complete or state.bar_time.time() > time(15, 18):
            return ()
        signals: list[EntrySignal] = []
        for strategy in strategies:
            if strategy.stock_code != state.stock_code:
                continue
            pair_code = PAIR_CODE[(strategy.entry_fast_period, strategy.entry_slow_period)]
            expected = 1 if strategy.direction == "LONG" else -1
            lead_time = None
            family = strategy.entry_family_code
            if family == "F1":
                matched = state.flow_crosses.get(pair_code) == expected
            elif family == "F2":
                matched = state.velocity_crosses.get(pair_code) == expected
            elif family == "F3":
                candidates = [
                    row.bar_time
                    for row in recent_states
                    if row.business_date == state.business_date
                    and row.is_complete
                    and state.bar_time - timedelta(minutes=5) <= row.bar_time < state.bar_time
                    and row.velocity_crosses.get(pair_code) == expected
                ]
                lead_time = max(candidates) if candidates else None
                matched = state.flow_crosses.get(pair_code) == expected and lead_time is not None
            elif family == "F4":
                absorption = state.long_absorption if expected == 1 else state.short_absorption
                matched = state.flow_crosses.get(pair_code) == expected and absorption
            else:
                raise ValueError(f"Unsupported FLOW V3 entry family: {family}")
            if not matched or not self._program_condition(strategy, state, expected):
                continue
            timestamp_key = (
                state.bar_time.strftime("%Y%m%d%H%M%S")
                + f"{state.bar_time.microsecond // 1000:03d}"
            )
            key = (
                f"FLOWV3|{state.stock_code}|{strategy.direction}|{family}|"
                f"{strategy.entry_fast_period}-{strategy.entry_slow_period}|"
                f"{strategy.program_condition_code}|{timestamp_key}"
            )
            signals.append(EntrySignal(strategy, state, key, lead_time))
        return tuple(signals)

    @staticmethod
    def _program_condition(
        strategy: StrategyContract, state: MinuteState, expected: int
    ) -> bool:
        code = strategy.program_condition_code
        if code == "P0":
            return True
        if state.program_net_flow is None or state.program_source_gap:
            return False
        opposite = Decimal(expected) * state.program_net_flow < 0
        if code == "P1":
            return opposite
        if code == "P2":
            return (
                opposite
                and state.program_velocity is not None
                and Decimal(expected) * state.program_velocity < 0
            )
        raise ValueError(f"Unsupported FLOW V3 program condition: {code}")

    @staticmethod
    def exit_cross_direction(strategy: StrategyContract) -> tuple[str, int]:
        source = "VELOCITY" if strategy.entry_family_code == "F2" else "FLOW"
        direction = -1 if strategy.direction == "LONG" else 1
        return source, direction
