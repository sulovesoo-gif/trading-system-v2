"""Research-only COMPLETE replay.

This module deliberately shares the live canonical signal function.  It never
touches RAW, alerts, system switches, or order clients.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Iterable

from src.analysis.event.multi_ma_event import detect_signals
from src.analysis.feature.multi_ma_feature import MultiMaFeature, build_daily_ma_features, build_multi_ma_features
from src.analysis.feature.sma_feature import MinuteBar

CAPITAL = Decimal("10000000")
MONEY = Decimal("0.01")
SINGLE = {"SIGNAL_1": {"SIGNAL_1"}, "SIGNAL_2": {"SIGNAL_2"}, "SIGNAL_3": {"SIGNAL_3"}}
ACCUMULATED = {
    "ACCUMULATED": {"SIGNAL_1", "SIGNAL_2", "SIGNAL_3"},
    "ACCUMULATED_1": {"SIGNAL_1", "SIGNAL_2"},
    "ACCUMULATED_2": {"SIGNAL_2", "SIGNAL_3"},
}
STRATEGIES = tuple((*SINGLE, *ACCUMULATED))


@dataclass(frozen=True)
class ResearchSignal:
    at: datetime
    signal_type: str
    direction: str
    feature: MultiMaFeature


@dataclass(frozen=True)
class ResearchLeg:
    signal_type: str
    signal_time: datetime
    entry_time: datetime
    entry_price: Decimal
    ratio: Decimal
    quantity: int
    invested_amount: Decimal


@dataclass
class Position:
    direction: str
    entry_signal: ResearchSignal
    entry_confirm_time: datetime
    legs: list[ResearchLeg] = field(default_factory=list)

    @property
    def ratio(self) -> Decimal:
        return sum((leg.ratio for leg in self.legs), Decimal("0"))

    @property
    def invested_amount(self) -> Decimal:
        return sum((leg.invested_amount for leg in self.legs), Decimal("0"))

    @property
    def average_entry_price(self) -> Decimal:
        if not self.legs:
            return Decimal("0")
        return sum((leg.entry_price * leg.invested_amount for leg in self.legs), Decimal("0")) / self.invested_amount


@dataclass(frozen=True)
class ClosedCycle:
    strategy_code: str
    direction: str
    entry_signal_time: datetime
    entry_confirm_time: datetime
    entry_price: Decimal
    exit_signal_time: datetime
    exit_time: datetime
    exit_price: Decimal
    quantity: int
    invested_amount: Decimal
    realized_profit: Decimal
    invested_return_rate: Decimal
    capital_return_rate: Decimal
    exit_type: str
    data_status: str
    legs: tuple[ResearchLeg, ...]
    gross_realized_profit: Decimal = Decimal("0")
    buy_fee: Decimal = Decimal("0")
    sell_fee: Decimal = Decimal("0")
    sell_tax: Decimal = Decimal("0")

    @property
    def total_trading_cost(self) -> Decimal:
        return self.buy_fee + self.sell_fee + self.sell_tax


def _session_id(value: datetime) -> str | None:
    moment = value.time()
    if moment.hour == 8 and moment.minute < 50:
        return "NXT_PREMARKET"
    if (moment.hour, moment.minute) >= (9, 0) and (moment.hour, moment.minute) <= (15, 19):
        return "KRX_REGULAR"
    if (moment.hour, moment.minute) >= (15, 40) and (moment.hour, moment.minute) <= (20, 0):
        return "NXT_AFTERMARKET"
    return None


class CompleteReplay:
    """Replay COMPLETE bars with an explicit, reproducible entry policy.

    Every non-contiguous minute/session is a hard feature boundary.  MA values
    never bridge lunch/auction/NXT gaps merely because rows happen to be next
    to each other in SQL order.
    """
    SIGNAL_ONLY = "SIGNAL_ONLY"
    MA10_CONFIRM = "MA10_CONFIRM"
    MA_CONFIRM = "MA_CONFIRM"
    MA_CONFIRM_INTEGRATED = "MA_CONFIRM_INTEGRATED"
    MA_AT_SIGNAL = "MA_AT_SIGNAL"

    @property
    def uses_confirmation(self) -> bool:
        return self.entry_condition in {self.MA10_CONFIRM, self.MA_CONFIRM, self.MA_CONFIRM_INTEGRATED}

    def __init__(self, *, short: int = 3, mid: int = 5, long: int = 10, price_field: str = "CLOSE", fee_rate: Decimal = Decimal("0"), sell_tax_rate: Decimal = Decimal("0"), slippage_rate: Decimal = Decimal("0"), entry_condition: str = MA10_CONFIRM, confirm_period: int = 10):
        self.short, self.mid, self.long, self.price_field = short, mid, long, price_field
        if entry_condition not in {self.SIGNAL_ONLY, self.MA10_CONFIRM, self.MA_CONFIRM, self.MA_CONFIRM_INTEGRATED, self.MA_AT_SIGNAL}:
            raise ValueError(f"unsupported entry_condition: {entry_condition}")
        if not isinstance(confirm_period, int) or confirm_period <= 0:
            raise ValueError("confirm_period must be a positive integer")
        self.fee_rate, self.sell_tax_rate = fee_rate, sell_tax_rate
        self.slippage_rate, self.entry_condition, self.confirm_period = slippage_rate, entry_condition, confirm_period

    def features(self, bars: Iterable[MinuteBar]) -> list[MultiMaFeature]:
        ordered = sorted(bars, key=lambda item: item.bar_time)
        groups: list[list[MinuteBar]] = []
        for bar in ordered:
            if _session_id(bar.bar_time) is None:
                continue
            if not groups or bar.bar_time.date() != groups[-1][-1].bar_time.date() or _session_id(bar.bar_time) != _session_id(groups[-1][-1].bar_time) or bar.bar_time - groups[-1][-1].bar_time != timedelta(minutes=1):
                groups.append([bar])
            else:
                groups[-1].append(bar)
        return [replace(feature, confirm_ma=feature.ma_long)
                for group in groups
                for feature in build_multi_ma_features(group, short_period=self.short, mid_period=self.mid, long_period=self.long, price_field=self.price_field)]

    @staticmethod
    def is_boundary(previous: MultiMaFeature | None, current: MultiMaFeature) -> bool:
        """A minute/session boundary; subclasses can retain the same state machine."""
        return (previous is None or previous.bar.bar_time.date() != current.bar.bar_time.date()
                or _session_id(previous.bar.bar_time) != _session_id(current.bar.bar_time)
                or current.bar.bar_time - previous.bar.bar_time != timedelta(minutes=1))

    def run(self, bars: list[MinuteBar]) -> tuple[list[MultiMaFeature], list[ResearchSignal], list[ClosedCycle]]:
        features = self.features(bars)
        target_prices = {bar.bar_time: bar.close_price for bar in bars}
        signals = self.canonical_signals(features)
        cycles = self.replay(features=features, signals=signals, target_prices=target_prices)
        return features, signals, cycles

    def canonical_signals(self, features: list[MultiMaFeature]) -> list[ResearchSignal]:
        result: list[ResearchSignal] = []
        for previous, current in zip(features, features[1:]):
            if self.is_boundary(previous, current):
                continue
            result.extend(ResearchSignal(current.bar.bar_time, item.signal_type, item.direction, current) for item in detect_signals(previous, current))
        return result

    def replay(self, *, features: list[MultiMaFeature], signals: list[ResearchSignal], target_prices: dict[datetime, Decimal], direction_transform: str = "DIRECT") -> list[ClosedCycle]:
        """Run all six official strategies. target_prices must be exact-time only."""
        output: list[ClosedCycle] = []
        by_time: dict[datetime, list[ResearchSignal]] = defaultdict(list)
        for signal in signals:
            direction = ({"LONG": "SHORT", "SHORT": "LONG"}.get(signal.direction, signal.direction)
                         if direction_transform == "INVERT" else signal.direction)
            by_time[signal.at].append(ResearchSignal(signal.at, signal.signal_type, direction, signal.feature))
        for code in STRATEGIES:
            output.extend(self._replay_strategy(code, features, by_time, target_prices))
        return output

    def _replay_strategy(self, code: str, features: list[MultiMaFeature], by_time: dict[datetime, list[ResearchSignal]], target_prices: dict[datetime, Decimal]) -> list[ClosedCycle]:
        allowed = SINGLE.get(code) or ACCUMULATED[code]
        position: Position | None = None
        pending: ResearchSignal | None = None
        closed: list[ClosedCycle] = []
        previous: MultiMaFeature | None = None
        for feature in features:
            now = feature.bar.bar_time
            boundary = self.is_boundary(previous, feature)
            if boundary:
                if position is not None and previous is not None:
                    previous_price = target_prices.get(previous.bar.bar_time)
                    if previous_price is not None:
                        closed.append(self._close(code, position, signal_time=previous.bar.bar_time, exit_time=previous.bar.bar_time, exit_price=previous_price, exit_type="SESSION_CLOSE"))
                    position = None
                # Do not carry a pending signal across a gap/session.
                pending = None
            ma_direction = self._ma10_direction(previous, feature)
            events = [event for event in by_time.get(now, ()) if event.signal_type in allowed]
            directions = {event.direction for event in events}
            if len(directions) > 1:
                previous = feature
                continue  # Explicit conflict: keep position, never guess ordering.
            direction = next(iter(directions), None)
            price = target_prices.get(now)
            if direction and pending and direction != pending.direction:
                pending = None
            # A valid reverse always exits the entire old virtual position.
            if position and direction and direction != position.direction:
                if price is not None:
                    closed.append(self._close(code, position, signal_time=now, exit_time=now, exit_price=price, exit_type="SIGNAL"))
                    position = None
                else:
                    previous = feature
                    continue  # exact target price is mandatory; no substitution.
            if position is None:
                candidate = next((event for event in events if event.direction == direction), None)
                if candidate and price is not None:
                    if self.entry_condition == self.SIGNAL_ONLY:
                        position = self._open(code, candidate, now, price, events)
                        pending = None
                    elif ma_direction == direction:
                        position = self._open(code, candidate, now, price, events)
                        pending = None
                    elif self.uses_confirmation:
                        pending = candidate
                elif pending and price is not None and ma_direction == pending.direction:
                    position = self._open(code, pending, now, price, [pending])
                    pending = None
            elif code in ACCUMULATED and direction == position.direction and price is not None:
                used = {leg.signal_type for leg in position.legs}
                fresh = [event for event in events if event.signal_type not in used]
                for event in fresh:
                    position.legs.append(self._leg(event, now, price, Decimal("1") / Decimal("3")))
            previous = feature
        if position and features:
            last = features[-1]
            price = target_prices.get(last.bar.bar_time)
            if price is not None:
                closed.append(self._close(code, position, signal_time=last.bar.bar_time, exit_time=last.bar.bar_time, exit_price=price, exit_type="SESSION_CLOSE"))
        return closed

    def _ma10_direction(self, previous: MultiMaFeature | None, current: MultiMaFeature) -> str | None:
        """Compatibility name: returns the configured confirmation MA slope."""
        # Hand-built test/legacy features do not carry confirm_ma; MA10 is the
        # backwards-compatible default confirmation series.
        previous_ma = (previous.confirm_ma if previous and previous.confirm_ma is not None else (previous.ma_long if previous else None))
        current_ma = current.confirm_ma if current.confirm_ma is not None else current.ma_long
        if previous_ma is None or current_ma is None:
            return None
        if current_ma > previous_ma:
            return "LONG"
        if current_ma < previous_ma:
            return "SHORT"
        return None

    def _open(self, code: str, signal: ResearchSignal, at: datetime, price: Decimal, same_time_events: list[ResearchSignal]) -> Position:
        if code in SINGLE:
            legs = [self._leg(signal, at, price, Decimal("1"))]
        else:
            unique = {event.signal_type: event for event in same_time_events if event.direction == signal.direction}
            legs = [self._leg(event, at, price, Decimal("1") / Decimal("3")) for event in unique.values()]
        return Position(signal.direction, signal, at, legs)

    def _leg(self, signal: ResearchSignal, at: datetime, price: Decimal, ratio: Decimal) -> ResearchLeg:
        # A configurable slippage policy changes the executable price, never
        # the RAW/feature price.  Zero is a valid explicit research policy.
        adjusted = price * (Decimal("1") + self.slippage_rate if signal.direction == "LONG" else Decimal("1") - self.slippage_rate)
        price = adjusted
        quantity = int((CAPITAL * ratio) // price)
        invested = price * quantity
        return ResearchLeg(signal.signal_type, signal.at, at, price, ratio, quantity, invested)

    def _close(self, code: str, position: Position, *, signal_time: datetime, exit_time: datetime, exit_price: Decimal, exit_type: str) -> ClosedCycle:
        exit_price = exit_price * (Decimal("1") - self.slippage_rate if position.direction == "LONG" else Decimal("1") + self.slippage_rate)
        profit = sum(((exit_price - leg.entry_price if position.direction == "LONG" else leg.entry_price - exit_price) * leg.quantity for leg in position.legs), Decimal("0"))
        invested = position.invested_amount
        quantity = sum(leg.quantity for leg in position.legs)
        # Persisted money is won-rounded.  Round each booked cost first so
        # the DB-level audit identity holds exactly, not merely before scale.
        profit = profit.quantize(MONEY)
        buy_fee = (invested * self.fee_rate).quantize(MONEY)
        sell_notional = exit_price * quantity
        sell_fee = (sell_notional * self.fee_rate).quantize(MONEY)
        sell_tax = (sell_notional * self.sell_tax_rate).quantize(MONEY)
        net = profit - buy_fee - sell_fee - sell_tax
        return ClosedCycle(code, position.direction, position.entry_signal.at, position.entry_confirm_time, position.average_entry_price,
                           signal_time, exit_time, exit_price, quantity, invested, net,
                           Decimal("0") if not invested else net / invested * Decimal("100"), net / CAPITAL * Decimal("100"),
                           exit_type, "NORMAL", tuple(position.legs), profit, buy_fee, sell_fee, sell_tax)


class DailyCompleteReplay(CompleteReplay):
    """Daily CLOSE replay sharing the exact six-strategy canonical state machine.

    Daily continuity means consecutive stored trading dates, not consecutive
    calendar days: weekends and holidays never manufacture a DATA_GAP.
    """
    def features(self, bars: Iterable[MinuteBar]) -> list[MultiMaFeature]:
        ordered = sorted(bars, key=lambda item: item.bar_time)
        features = build_daily_ma_features(ordered, price_field=self.price_field)
        values = [bar.close_price for bar in ordered]
        confirmation = {
            ordered[index].bar_time: sum(values[index - self.confirm_period + 1:index + 1]) / Decimal(self.confirm_period)
            for index in range(self.confirm_period - 1, len(ordered))
        }
        return [replace(feature, confirm_ma=confirmation.get(feature.bar.bar_time)) for feature in features]

    @staticmethod
    def is_boundary(previous: MultiMaFeature | None, current: MultiMaFeature) -> bool:
        return previous is None


class RegularAfterContinuousReplay(CompleteReplay):
    """Minute COMPLETE replay that carries regular-session MA state into NXT after.

    The 15:20~15:39 auction/transition window remains excluded; it is not a
    feature and therefore cannot emit a signal or become a synthetic bar.
    """
    def features(self, bars: Iterable[MinuteBar]) -> list[MultiMaFeature]:
        groups: list[list[MinuteBar]] = []
        for bar in sorted(bars, key=lambda item: item.bar_time):
            session = _session_id(bar.bar_time)
            if session is None:
                continue
            premarket = session == "NXT_PREMARKET"
            if (not groups or bar.bar_time.date() != groups[-1][-1].bar_time.date()
                    or (_session_id(groups[-1][-1].bar_time) == "NXT_PREMARKET") != premarket):
                groups.append([bar])
            else:
                groups[-1].append(bar)
        return [replace(feature, confirm_ma=feature.ma_long)
                for group in groups
                for feature in build_multi_ma_features(group, short_period=self.short, mid_period=self.mid, long_period=self.long, price_field=self.price_field)]

    @staticmethod
    def is_boundary(previous: MultiMaFeature | None, current: MultiMaFeature) -> bool:
        if previous is None or previous.bar.bar_time.date() != current.bar.bar_time.date():
            return True
        return (_session_id(previous.bar.bar_time) == "NXT_PREMARKET") != (_session_id(current.bar.bar_time) == "NXT_PREMARKET")
