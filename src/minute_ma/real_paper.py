"""Pure contracts for the Minute-MA + REAL PAPER research path.

This module deliberately has no broker/LIVE dependency.  It is shared by the
historical loader and the incremental runtime so the two paths use identical
filter, K and accounting semantics.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
from typing import Iterable


INITIAL_CAPITAL = Decimal("10000000")
BUY_FEE_RATE = Decimal("0.000140527")
SELL_FEE_RATE = Decimal("0.000140527")
SELL_TAX_RATE = Decimal("0.002")
ENTRY_START = time(15, 0)
ENTRY_END = time(15, 18)


class RealFilter(str, Enum):
    F1 = "REAL_F1"
    F2 = "REAL_F2"
    F3 = "REAL_F3"


@dataclass(frozen=True)
class RealSnapshot:
    velocity_value: Decimal | None
    velocity_avg_3: Decimal | None
    velocity_avg_10: Decimal | None
    flow_avg_5: Decimal | None
    flow_avg_20: Decimal | None
    is_complete: bool


@dataclass(frozen=True)
class CandidateTrade:
    key: str
    trading_date: date
    entry_time: datetime
    exit_time: datetime | None
    entry_price: Decimal
    exit_price: Decimal | None


@dataclass(frozen=True)
class AccountedTrade:
    candidate: CandidateTrade
    slot_no: int | None
    quantity: int
    capital_before: Decimal | None
    capital_after: Decimal | None
    gross_pnl: Decimal
    buy_fee: Decimal
    sell_fee: Decimal
    sell_tax: Decimal
    realized_pnl: Decimal
    status: str


def eligible_entry_time(at: datetime) -> bool:
    minute=at.time().replace(second=0,microsecond=0)
    return ENTRY_START <= minute <= ENTRY_END


def passing_filters(snapshot: RealSnapshot) -> tuple[RealFilter, ...]:
    """Return nested F1/F2/F3 passes; UNKNOWN never passes."""
    if not snapshot.is_complete or snapshot.velocity_value is None:
        return ()
    f1 = snapshot.velocity_value > 0
    f2 = (f1 and snapshot.velocity_avg_3 is not None
          and snapshot.velocity_avg_10 is not None
          and snapshot.velocity_avg_3 > 0 and snapshot.velocity_avg_10 > 0)
    f3 = (f2 and snapshot.flow_avg_5 is not None
          and snapshot.flow_avg_20 is not None
          and snapshot.flow_avg_5 > 0 and snapshot.flow_avg_20 > 0)
    return tuple(code for code, passed in (
        (RealFilter.F1, f1), (RealFilter.F2, f2), (RealFilter.F3, f3)
    ) if passed)


def daily_max_concurrency(trades: Iterable[CandidateTrade],
                          *, market_dates: Iterable[date] | None = None) -> dict[date, int]:
    """04A2 contract: EXIT precedes ENTRY at an equal timestamp."""
    events: dict[date, list[tuple[datetime, int]]] = defaultdict(list)
    for trade in trades:
        # Overnight positions count at the start of every intervening market
        # date represented by an event.  The historical loader calls this with
        # trading-date-complete candidates, so entry/exit day events suffice.
        events[trade.entry_time.date()].append((trade.entry_time, 1))
        if trade.exit_time is not None:
            events[trade.exit_time.date()].append((trade.exit_time, -1))
    calendar = set(events)
    if market_dates is not None:
        calendar.update(market_dates)
    result: dict[date, int] = {}
    carry = 0
    for day in sorted(calendar):
        current = carry
        maximum = current
        for _, delta in sorted(events[day], key=lambda item: (item[0], item[1])):
            current += delta
            maximum = max(maximum, current)
        result[day] = maximum
        carry = current
    return result


def k_mode(trades: Iterable[CandidateTrade],
           *, market_dates: Iterable[date] | None = None) -> int:
    values = [value for value in daily_max_concurrency(
        trades, market_dates=market_dates).values() if value > 0]
    if not values:
        return 1
    counts = Counter(values)
    highest = max(counts.values())
    return min(value for value, count in counts.items() if count == highest)


def replay_slots(trades: Iterable[CandidateTrade], *, slot_count: int,
                 initial_capital: Decimal = INITIAL_CAPITAL) -> tuple[AccountedTrade, ...]:
    if slot_count < 1:
        raise ValueError("slot_count must be positive")
    slot_capital = [initial_capital / slot_count for _ in range(slot_count)]
    busy_until: list[datetime | None] = [None] * slot_count
    results: list[AccountedTrade] = []
    ordered=sorted(trades,key=lambda row:(row.entry_time,row.exit_time or datetime.max,row.key))
    for trade in ordered:
        slot = next((index for index, until in enumerate(busy_until)
                     if until is None or until <= trade.entry_time), None)
        if slot is None:
            results.append(AccountedTrade(trade, None, 0, None, None,
                                           Decimal(0), Decimal(0), Decimal(0),
                                           Decimal(0), Decimal(0), "SKIPPED_NO_SLOT"))
            continue
        before = slot_capital[slot]
        unit_cost = trade.entry_price * (Decimal(1) + BUY_FEE_RATE)
        qty = int((before / unit_cost).to_integral_value(rounding=ROUND_FLOOR))
        if qty <= 0:
            results.append(AccountedTrade(trade, slot + 1, 0, before, before,
                                           Decimal(0), Decimal(0), Decimal(0),
                                           Decimal(0), Decimal(0), "SKIPPED_QTY_ZERO"))
            continue
        quantity = Decimal(qty)
        if trade.exit_time is None or trade.exit_price is None:
            busy_until[slot]=datetime.max
            results.append(AccountedTrade(trade,slot+1,qty,before,None,
                                           Decimal(0),Decimal(0),Decimal(0),
                                           Decimal(0),Decimal(0),"OPEN"))
            continue
        gross = quantity * (trade.exit_price - trade.entry_price)
        buy_fee = quantity * trade.entry_price * BUY_FEE_RATE
        sell_fee = quantity * trade.exit_price * SELL_FEE_RATE
        sell_tax = quantity * trade.exit_price * SELL_TAX_RATE
        realized = gross - buy_fee - sell_fee - sell_tax
        after = before + realized
        slot_capital[slot] = after
        busy_until[slot] = trade.exit_time
        results.append(AccountedTrade(trade, slot + 1, qty, before, after,
                                       gross, buy_fee, sell_fee, sell_tax,
                                       realized, "CLOSED"))
    return tuple(results)
