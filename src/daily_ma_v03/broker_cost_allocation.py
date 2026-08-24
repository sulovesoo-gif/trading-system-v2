"""Daily MA broker-cost snapshots and deterministic product-day allocation.

KIS does not expose a stable per-fill identifier or an order-level final fee.
This module therefore treats one *final* broker ``trade_date x execution_code``
cost total as authoritative.  It never manufactures a broker fill ID and it
never settles strategy capital until the final snapshot has been allocated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_FLOOR
from enum import Enum


class BrokerCostStatus(str, Enum):
    PENDING_BROKER_COST = "PENDING_BROKER_COST"
    FINALIZED = "FINALIZED"
    BROKER_COST_ATTRIBUTION_BLOCKED = "BROKER_COST_ATTRIBUTION_BLOCKED"
    BROKER_COST_SNAPSHOT_REGRESSION = "BROKER_COST_SNAPSHOT_REGRESSION"


@dataclass(frozen=True)
class BrokerCostTotals:
    buy_fee: Decimal = Decimal("0")
    sell_fee: Decimal = Decimal("0")
    sell_tax: Decimal = Decimal("0")
    other_cost: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if any(value < 0 for value in (self.buy_fee, self.sell_fee, self.sell_tax, self.other_cost)):
            raise ValueError("BROKER_COST_NEGATIVE")


@dataclass(frozen=True)
class CostAllocationTarget:
    live_trade_id: int
    side: str
    fill_notional: Decimal
    stable_key: str

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"} or self.fill_notional < 0:
            raise ValueError("BROKER_COST_TARGET_INVALID")


@dataclass(frozen=True)
class BrokerCostSnapshot:
    trade_date: date
    execution_stock_code: str
    totals: BrokerCostTotals
    broker_snapshot_at: datetime
    final: bool
    status: BrokerCostStatus


@dataclass(frozen=True)
class CostAllocation:
    live_trade_id: int
    side: str
    fill_notional: Decimal
    buy_fee: Decimal = Decimal("0")
    sell_fee: Decimal = Decimal("0")
    sell_tax: Decimal = Decimal("0")
    other_cost: Decimal = Decimal("0")


def _allocate(total: Decimal, targets: tuple[CostAllocationTarget, ...]) -> dict[tuple[int, str], Decimal]:
    """Allocate whole won pro-rata, then residual by stable deterministic key."""
    if total == 0:
        return {(target.live_trade_id, target.side): Decimal("0") for target in targets}
    denominator = sum((target.fill_notional for target in targets), Decimal("0"))
    if denominator <= 0:
        raise ValueError("BROKER_COST_DENOMINATOR_REQUIRED")
    floors = {
        (target.live_trade_id, target.side): (total * target.fill_notional / denominator).quantize(Decimal("1"), rounding=ROUND_FLOOR)
        for target in targets
    }
    residual = int(total - sum(floors.values(), Decimal("0")))
    ordered = sorted(targets, key=lambda target: (target.stable_key, target.live_trade_id))
    for target in ordered[:residual]:
        floors[(target.live_trade_id, target.side)] += Decimal("1")
    return floors


def allocate_final_costs(*, snapshot: BrokerCostSnapshot, targets: tuple[CostAllocationTarget, ...],
                         unattributed_activity: bool = False) -> tuple[BrokerCostStatus, tuple[CostAllocation, ...]]:
    """Allocate only a final, attributable snapshot; otherwise fail closed."""
    if unattributed_activity:
        return BrokerCostStatus.BROKER_COST_ATTRIBUTION_BLOCKED, ()
    if not snapshot.final or snapshot.status is not BrokerCostStatus.FINALIZED:
        return BrokerCostStatus.PENDING_BROKER_COST, ()
    buys = tuple(target for target in targets if target.side == "BUY")
    sells = tuple(target for target in targets if target.side == "SELL")
    buy_fee = _allocate(snapshot.totals.buy_fee, buys)
    sell_fee = _allocate(snapshot.totals.sell_fee, sells)
    sell_tax = _allocate(snapshot.totals.sell_tax, sells)
    # Other cost requires an explicit source classification.  V0.4.2 records
    # it only after the adapter has chosen BUY or SELL; generic mixed costs
    # are intentionally blocked rather than inferred.
    if snapshot.totals.other_cost:
        raise ValueError("BROKER_OTHER_COST_BASIS_REQUIRED")
    rows = tuple(CostAllocation(
        live_trade_id=target.live_trade_id, side=target.side, fill_notional=target.fill_notional,
        buy_fee=buy_fee.get((target.live_trade_id, target.side), Decimal("0")),
        sell_fee=sell_fee.get((target.live_trade_id, target.side), Decimal("0")),
        sell_tax=sell_tax.get((target.live_trade_id, target.side), Decimal("0")),
    ) for target in sorted(targets, key=lambda target: (target.stable_key, target.live_trade_id)))
    if sum((row.buy_fee for row in rows), Decimal("0")) != snapshot.totals.buy_fee \
       or sum((row.sell_fee for row in rows), Decimal("0")) != snapshot.totals.sell_fee \
       or sum((row.sell_tax for row in rows), Decimal("0")) != snapshot.totals.sell_tax:
        raise AssertionError("BROKER_COST_ALLOCATION_NOT_RECONCILED")
    return BrokerCostStatus.FINALIZED, rows


def classify_snapshot(*, stored: BrokerCostSnapshot | None, observed: BrokerCostSnapshot) -> BrokerCostStatus:
    """A final authoritative snapshot may not later decrease or mutate."""
    if stored is None:
        return observed.status
    prior = stored.totals
    current = observed.totals
    decreased = any(current_value < prior_value for current_value, prior_value in zip(
        (current.buy_fee, current.sell_fee, current.sell_tax, current.other_cost),
        (prior.buy_fee, prior.sell_fee, prior.sell_tax, prior.other_cost),
    ))
    changed = current != prior
    if stored.status is BrokerCostStatus.FINALIZED and (decreased or changed):
        return BrokerCostStatus.BROKER_COST_SNAPSHOT_REGRESSION
    if decreased:
        return BrokerCostStatus.BROKER_COST_SNAPSHOT_REGRESSION
    return observed.status
