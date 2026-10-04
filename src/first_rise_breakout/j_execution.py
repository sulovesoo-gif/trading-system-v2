"""FIRST_RISE-specific planning rules; no transport, network or global gates.

The production adapter must hold the FIRST_RISE capital/claim lock across this
decision and durable request creation. Fresh KIS cash is queried inside that
serialized section. Other strategies' capital allocations are not inputs.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from .j_capital import size_buy

STRATEGY_ID = 'FIRST_RISE_J_V1.3'


@dataclass(frozen=True)
class EntryEligibility:
    reason: str
    quantity: int = 0
    cash_required: Decimal = Decimal(0)
    capital_epoch_id: UUID | None = None
    capital_epoch_revision: int | None = None
    sizing_evidence: dict | None = None


def plan_epoch_entry(*, epoch, config, **kwargs):
    """Use locked current-epoch PnL, never a cross-epoch aggregate.

    Caller obtains epoch with JCapitalEpochRepository.entry_state() inside the
    ENTRY transaction and binds the trade in that same transaction.
    """
    if (epoch.start_slot_amount,epoch.slot_step_amount,epoch.max_slot_amount) != (
            config.start_slot_amount,config.slot_step_amount,config.max_slot_amount):
        raise ValueError('FIRST_RISE_EPOCH_CONFIG_MISMATCH')
    result=plan_entry(config=config,realized_net_pnl=epoch.realized_net_pnl,**kwargs)
    return replace(result,capital_epoch_id=epoch.epoch_id,capital_epoch_revision=epoch.revision)


def plan_entry(*, config, signal_time: datetime, signal_sequence: int,
               prior_exit_reason: str | None, prior_exit_time: datetime | None,
               effective_from: datetime, now: datetime, realized_net_pnl,
               broker_cash, price, buy_fee_rate, same_stock_pending_or_open: bool,
               unacknowledged_reservation=Decimal(0), capacity=None, liquidity=None):
    """No replay, no pyramiding, and no duplicate use of pre-ACK cash.

    ``unacknowledged_reservation`` covers ONLY this strategy's requests not
    reflected in a broker cash response. Never pass total OPEN exposure or
    accepted-order reservations here; KIS already accounts for those.
    """
    from datetime import time
    if signal_time < effective_from:
        return EntryEligibility('NO_REPLAY')
    if signal_time.date() != now.date() or signal_time > now:
        return EntryEligibility('SIGNAL_DATE_INVALID')
    if not (config.live_entry_start <= signal_time.time() < config.live_entry_cutoff
            and now.time() < config.live_entry_cutoff):
        return EntryEligibility('OUTSIDE_LIVE_ENTRY_WINDOW')
    if signal_sequence == 1:
        if signal_time.time() > time(10):
            return EntryEligibility('FIRST_AFTER_1000')
    elif signal_sequence == 2:
        if (signal_time.time() < time(10) or prior_exit_reason != 'STOP_ENTRY_BREAK'
                or prior_exit_time is None or signal_time <= prior_exit_time):
            return EntryEligibility('SECOND_NOT_ELIGIBLE')
    else:
        return EntryEligibility('THIRD_FORBIDDEN')
    # The repository separately proves parent ownership and terminal SELL state.
    # Only the eligible J SECOND may coexist with its FIRST residual lot.
    if same_stock_pending_or_open and signal_sequence != 2:
        return EntryEligibility('SAME_STOCK_PENDING_OR_OPEN')
    reservation = Decimal(unacknowledged_reservation)
    if not reservation.is_finite() or reservation < 0:
        raise ValueError('invalid FIRST_RISE unacknowledged reservation')
    cash = Decimal(broker_cash)
    if not cash.is_finite() or cash < 0:
        raise ValueError('invalid KIS cash')
    if liquidity is not None:
        from .v2_capacity import capacity_sizing
        if capacity is None:return EntryEligibility('INVALID_CAPACITY_CONFIG')
        qty,used,evidence=capacity_sizing(config=config,capacity=capacity,realized_net_pnl=realized_net_pnl,
            broker_cash=cash,reservation=reservation,price=price,buy_fee_rate=buy_fee_rate,liquidity=liquidity)
        reason='READY' if qty else liquidity.get('reason') if liquidity.get('reason')!='OK' else 'NO_CAPITAL'
        return EntryEligibility(reason,qty,used,sizing_evidence=evidence)
    sizing = size_buy(config=config, realized_net_pnl=realized_net_pnl,
        broker_cash=max(Decimal(0), cash-reservation), price=price, buy_fee_rate=buy_fee_rate)
    if sizing.quantity < 1:
        return EntryEligibility('NO_CAPITAL')
    return EntryEligibility('READY', sizing.quantity, sizing.estimated_cash_used)


def owned_sell_quantity(*, strategy_id, position_strategy_id, operation_id,
                        position_operation_id, trade_id, position_trade_id,
                        owned_quantity, pending_sell_quantity):
    """Never size a strategy exit from the account's total stock holding."""
    if (strategy_id != STRATEGY_ID or position_strategy_id != strategy_id
            or not operation_id or operation_id != position_operation_id
            or not trade_id or trade_id != position_trade_id):
        raise ValueError('FIRST_RISE_OWNERSHIP_MISMATCH')
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0
           for x in (owned_quantity, pending_sell_quantity)):
        raise ValueError('invalid ownership quantity')
    if pending_sell_quantity > owned_quantity:
        raise ValueError('pending SELL exceeds owned quantity')
    return owned_quantity-pending_sell_quantity
