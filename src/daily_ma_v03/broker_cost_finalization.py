"""T+1 KRX-trading-day stable recheck policy for Daily MA broker costs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .broker_cost_allocation import BrokerCostSnapshot, BrokerCostStatus


@dataclass(frozen=True)
class StableCostRecheck:
    snapshot: BrokerCostSnapshot
    fill_set_fingerprint: str
    unattributed_activity: bool
    confirmation_count: int = 0
    last_confirmed_at: datetime | None = None


def next_krx_trading_date(*, trade_date: date, calendar) -> date:
    """Use the official KRX calendar, never calendar-day arithmetic."""
    candidates = calendar.open_dates(trade_date + timedelta(days=1), trade_date + timedelta(days=14))
    if not candidates:
        raise RuntimeError("NEXT_KRX_TRADING_DAY_REQUIRED")
    return candidates[0]


def stable_recheck(*, stored: StableCostRecheck | None, observed: BrokerCostSnapshot,
                   fill_set_fingerprint: str, unattributed_activity: bool,
                   next_trade_date: date, minimum_interval: timedelta = timedelta(minutes=10)) -> StableCostRecheck:
    """Require two T+1-or-later identical cost *and fill-set* observations."""
    if observed.broker_snapshot_at.date() < next_trade_date:
        return StableCostRecheck(observed, fill_set_fingerprint, unattributed_activity, 0, observed.broker_snapshot_at)
    if unattributed_activity:
        return StableCostRecheck(observed, fill_set_fingerprint, True, 0, observed.broker_snapshot_at)
    same = stored is not None and stored.snapshot.totals == observed.totals \
        and stored.fill_set_fingerprint == fill_set_fingerprint and not stored.unattributed_activity
    elapsed = stored is not None and stored.last_confirmed_at is not None \
        and observed.broker_snapshot_at - stored.last_confirmed_at >= minimum_interval
    confirmations = stored.confirmation_count + 1 if same and elapsed else 1
    final = confirmations >= 2
    status = BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK if final else BrokerCostStatus.PENDING_BROKER_COST
    snapshot = BrokerCostSnapshot(observed.trade_date, observed.execution_stock_code, observed.totals,
                                  observed.broker_snapshot_at, final, status)
    return StableCostRecheck(snapshot, fill_set_fingerprint, False, confirmations, observed.broker_snapshot_at)
