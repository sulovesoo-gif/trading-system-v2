"""Build exact net settlement amounts only after both cost sides are final."""
from __future__ import annotations
from decimal import Decimal

from .broker_cost_allocation import CostAllocation
from .capital import SettlementAmounts


def settlement_amounts(*, live_trade_id: int, entry_filled_amount: Decimal, exit_filled_amount: Decimal,
                       allocations: tuple[CostAllocation, ...]) -> SettlementAmounts:
    """Capital repository supplies exactly-once protection by ``live_trade_id``."""
    rows = tuple(row for row in allocations if row.live_trade_id == live_trade_id)
    buys = tuple(row for row in rows if row.side == 'BUY')
    sells = tuple(row for row in rows if row.side == 'SELL')
    if not buys or not sells:
        raise ValueError('PENDING_BROKER_COST')
    return SettlementAmounts(
        entry_filled_amount, exit_filled_amount,
        buy_fee=sum((row.buy_fee for row in buys), Decimal('0')),
        sell_fee=sum((row.sell_fee for row in sells), Decimal('0')),
        sell_tax=sum((row.sell_tax for row in sells), Decimal('0')),
        other_cost_amount=sum((row.other_cost for row in rows), Decimal('0')),
    )
