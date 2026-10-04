"""J strategy-wide realized PnL tiers and broker-cash-limited sizing.

No fixed strategy allocation, cross-strategy reservation, or live enable flag.
The caller must serialize pending claims and obtain fresh broker cash immediately
before POST. Broker cash already includes accepted-order reservations: never
subtract those (or other strategies' allocated capital) a second time.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from .config import FirstRiseRuntimeConfig


def finite_decimal(value, name):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class SlotTier:
    realized_net_pnl: Decimal
    compound_reference: Decimal
    common_slot_amount: Decimal


def slot_tier(config: FirstRiseRuntimeConfig, realized_net_pnl: Decimal) -> SlotTier:
    pnl = finite_decimal(realized_net_pnl, "realized_net_pnl")
    start = Decimal(config.start_slot_amount)
    step = Decimal(config.slot_step_amount)
    reference = start + pnl
    levels = max(Decimal(0), ((reference - start) / step).to_integral_value(rounding=ROUND_FLOOR))
    slot = min(Decimal(config.max_slot_amount), start + levels * step)
    return SlotTier(pnl, reference, slot)


@dataclass(frozen=True)
class BuySizing:
    quantity: int
    common_slot_amount: Decimal
    broker_cash: Decimal
    target_cash: Decimal
    estimated_cash_used: Decimal


def size_buy(*, config, realized_net_pnl, broker_cash, price, buy_fee_rate):
    cash = finite_decimal(broker_cash, "broker_cash")
    price = finite_decimal(price, "price")
    fee = finite_decimal(buy_fee_rate, "buy_fee_rate")
    if cash < 0 or price <= 0 or fee < 0:
        raise ValueError("invalid cash/price/fee")
    tier = slot_tier(config, realized_net_pnl)
    target = min(tier.common_slot_amount, cash)
    one_share = price * (Decimal(1) + fee)
    qty = int((target / one_share).to_integral_value(rounding=ROUND_FLOOR))
    return BuySizing(qty, tier.common_slot_amount, cash, target, qty * one_share)
