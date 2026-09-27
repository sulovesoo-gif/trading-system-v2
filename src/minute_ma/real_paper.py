"""Pure contracts for Minute-MA + REAL PAPER V1.3.

PAPER entries never reserve capital and are never gated by K/slots. Every
signal is independent. Only settled PnL changes capital used by later entries;
fixed-10M accounting is calculated from the same trade ledger.
"""
from __future__ import annotations

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
class CostResult:
    quantity: int
    gross_pnl: Decimal
    buy_fee: Decimal
    sell_fee: Decimal
    sell_tax: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True)
class AccountedTrade:
    candidate: CandidateTrade
    entry_realized_capital: Decimal
    settlement_realized_capital_after: Decimal | None
    compound: CostResult
    fixed: CostResult
    status: str


@dataclass(frozen=True)
class ReplayResult:
    trades: tuple[AccountedTrade, ...]
    current_realized_capital: Decimal


def eligible_entry_time(at: datetime) -> bool:
    minute = at.time().replace(second=0, microsecond=0)
    return ENTRY_START <= minute <= ENTRY_END


def passing_filters(snapshot: RealSnapshot) -> tuple[RealFilter, ...]:
    """Return nested F1/F2/F3 passes; incomplete or NULL input never passes."""
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


def purchasable_quantity(capital: Decimal, price: Decimal) -> int:
    if capital <= 0 or price <= 0:
        return 0
    return int((capital / (price * (Decimal(1) + BUY_FEE_RATE))).to_integral_value(
        rounding=ROUND_FLOOR))


def account_costs(quantity: int, entry_price: Decimal,
                  exit_price: Decimal | None) -> CostResult:
    if quantity <= 0 or exit_price is None:
        return CostResult(quantity, Decimal(0), Decimal(0), Decimal(0),
                          Decimal(0), Decimal(0))
    qty = Decimal(quantity)
    gross = qty * (exit_price - entry_price)
    buy_fee = qty * entry_price * BUY_FEE_RATE
    sell_fee = qty * exit_price * SELL_FEE_RATE
    sell_tax = qty * exit_price * SELL_TAX_RATE
    return CostResult(quantity, gross, buy_fee, sell_fee, sell_tax,
                      gross - buy_fee - sell_fee - sell_tax)


def replay_parallel_capital(
        trades: Iterable[CandidateTrade], *,
        initial_capital: Decimal = INITIAL_CAPITAL) -> ReplayResult:
    """Replay overlapping trades with EXIT-before-ENTRY ordering."""
    ordered = tuple(sorted(trades, key=lambda row: (row.entry_time, row.key)))
    by_key = {row.key: row for row in ordered}
    events: list[tuple[datetime, int, str]] = []
    for row in ordered:
        events.append((row.entry_time, 1, row.key))
        if row.exit_time is not None and row.exit_price is not None:
            events.append((row.exit_time, 0, row.key))
    current = initial_capital
    entries: dict[str, tuple[Decimal, CostResult, CostResult]] = {}
    settled_after: dict[str, Decimal] = {}
    for _, kind, key in sorted(events, key=lambda item: (item[0], item[1], item[2])):
        row = by_key[key]
        if kind == 1:
            entries[key] = (
                current,
                account_costs(purchasable_quantity(current, row.entry_price),
                              row.entry_price, row.exit_price),
                account_costs(purchasable_quantity(initial_capital, row.entry_price),
                              row.entry_price, row.exit_price),
            )
        else:
            _, compound, _ = entries[key]
            current += compound.realized_pnl
            settled_after[key] = current
    accounted = []
    for row in ordered:
        entry_capital, compound, fixed = entries[row.key]
        accounted.append(AccountedTrade(
            row, entry_capital, settled_after.get(row.key), compound, fixed,
            "CLOSED" if row.key in settled_after else "OPEN"))
    return ReplayResult(tuple(accounted), current)
