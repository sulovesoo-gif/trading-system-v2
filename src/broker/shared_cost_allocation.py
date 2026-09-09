"""One product/day allocation across Daily, Minute and FLOW ownership.

Reuses the established whole-won allocator; family names prevent trade-id
collisions. No fee rates, broker fills or finalization evidence are invented.
"""
from dataclasses import dataclass
from decimal import Decimal
from src.daily_ma_v03.broker_cost_allocation import CostAllocationTarget, allocate_final_costs


@dataclass(frozen=True)
class OwnedCheckpoint:
    family: str
    trade_id: int
    broker_order_id: str
    checkpoint_version: int
    side: str
    quantity: int
    amount: Decimal


def allocate_shared_costs(*, snapshot, checkpoints, unattributed_activity=False):
    grouped = {}
    seen = set()
    for fill in checkpoints:
        if fill.family not in ('DAILY','MINUTE','FLOW'):
            raise ValueError('UNKNOWN_COST_OWNER')
        if fill.side not in ('BUY','SELL') or fill.quantity<=0 or not fill.amount.is_finite() or fill.amount<=0:
            raise ValueError('INVALID_COST_CHECKPOINT')
        identity=(fill.broker_order_id,fill.checkpoint_version)
        if identity in seen:
            raise ValueError('DUPLICATE_CHECKPOINT_COST_OWNERSHIP')
        seen.add(identity)
        key=(fill.family,fill.trade_id,fill.side)
        grouped[key]=grouped.get(key,Decimal(0))+fill.amount
    keys=sorted(grouped)
    targets=tuple(CostAllocationTarget(i,key[2],grouped[key],f'{key[0]}|{key[1]:020d}|{key[2]}')
                  for i,key in enumerate(keys,1))
    status,allocations=allocate_final_costs(snapshot=snapshot,targets=targets,
                                           unattributed_activity=unattributed_activity)
    return status,tuple((keys[a.live_trade_id-1],a) for a in allocations)
