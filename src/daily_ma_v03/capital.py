"""V0.4 strategy-local realized-compound capital contracts.

This module is intentionally broker-transport free.  It only decides whether a
LIVE *no-send* entry may be prepared and records immutable inputs required for
later settlement.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR


@dataclass(frozen=True)
class CapitalEpoch:
    strategy_id: str
    capital_epoch_no: int
    epoch_initial_capital: Decimal
    cumulative_net_realized_pnl: Decimal

    @property
    def compound_capital(self) -> Decimal:
        return self.epoch_initial_capital + self.cumulative_net_realized_pnl


@dataclass(frozen=True)
class AvailableCash:
    amount: Decimal
    # KIS inquire-psbl-order reports orderable cash.  When true, pending order
    # reservations are already accounted for by the broker response.
    includes_pending_order_reservations: bool


@dataclass(frozen=True)
class EntryCapitalDecision:
    status: str
    quantity: int
    planned_notional: Decimal
    capital_at_signal: Decimal
    effective_available_cash: Decimal


def decide_entry(*, capital: CapitalEpoch, available_cash: AvailableCash,
                 reference_price: Decimal, locally_reserved_amount: Decimal = Decimal("0")) -> EntryCapitalDecision:
    """Return a fail-closed V0.4 entry decision without double-charging cash."""
    if reference_price <= 0:
        raise ValueError("reference_price must be positive")
    if capital.compound_capital <= 0:
        return EntryCapitalDecision("ZERO_QUANTITY", 0, Decimal("0"), capital.compound_capital, available_cash.amount)
    # Application reservations protect a shared cash balance only when the
    # broker's value does not already exclude pending orders.
    effective_cash = available_cash.amount if available_cash.includes_pending_order_reservations else max(
        Decimal("0"), available_cash.amount - locally_reserved_amount
    )
    quantity = int((capital.compound_capital / reference_price).to_integral_value(rounding=ROUND_FLOOR))
    if quantity <= 0:
        return EntryCapitalDecision("ZERO_QUANTITY", 0, Decimal("0"), capital.compound_capital, effective_cash)
    notional = reference_price * quantity
    if notional > effective_cash:
        return EntryCapitalDecision("INSUFFICIENT_AVAILABLE_CASH", quantity, notional, capital.compound_capital, effective_cash)
    return EntryCapitalDecision("NO_SEND_VALIDATED", quantity, notional, capital.compound_capital, effective_cash)


@dataclass(frozen=True)
class SettlementAmounts:
    entry_filled_amount: Decimal
    exit_filled_amount: Decimal
    buy_fee: Decimal = Decimal("0")
    sell_fee: Decimal = Decimal("0")
    sell_tax: Decimal = Decimal("0")
    other_cost_amount: Decimal = Decimal("0")

    @property
    def gross_realized_pnl(self) -> Decimal:
        return self.exit_filled_amount - self.entry_filled_amount

    @property
    def net_realized_pnl(self) -> Decimal:
        return self.gross_realized_pnl - self.buy_fee - self.sell_fee - self.sell_tax - self.other_cost_amount
