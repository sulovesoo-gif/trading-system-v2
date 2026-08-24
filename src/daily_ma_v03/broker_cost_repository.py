"""Durable V0.4.2 broker-cost snapshots; settlement remains separately gated."""
from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from .broker_cost_allocation import (
    BrokerCostSnapshot, BrokerCostStatus, CostAllocationTarget, allocate_final_costs, classify_snapshot,
)


class PostgresDailyMaBrokerCostStore:
    """Idempotently persists final cost allocation; never creates broker fills."""
    def __init__(self, connection_factory, *, commit: bool = True) -> None:
        self.connection_factory, self.commit = connection_factory, commit

    def apply(self, *, snapshot: BrokerCostSnapshot, targets: tuple[CostAllocationTarget, ...],
              unattributed_activity: bool = False) -> BrokerCostStatus:
        with self.connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT broker_cost_snapshot_id,broker_buy_fee,broker_sell_fee,broker_sell_tax,broker_other_cost,
                                     broker_snapshot_at,finalization_status
                                FROM daily_strategy_live_broker_cost_snapshot
                               WHERE trade_date=%s AND execution_stock_code=%s FOR UPDATE""",
                           (snapshot.trade_date, snapshot.execution_stock_code))
            row = cursor.fetchone()
            stored = None
            if row is not None:
                from .broker_cost_allocation import BrokerCostTotals
                stored = BrokerCostSnapshot(snapshot.trade_date, snapshot.execution_stock_code,
                    BrokerCostTotals(*row[1:5]), row[5], row[6] == BrokerCostStatus.FINALIZED.value,
                    BrokerCostStatus(row[6]))
            status = classify_snapshot(stored=stored, observed=snapshot)
            snapshot_id = str(uuid5(NAMESPACE_URL, f"daily-ma-v042-cost|{snapshot.trade_date}|{snapshot.execution_stock_code}"))
            if status is BrokerCostStatus.BROKER_COST_SNAPSHOT_REGRESSION:
                cursor.execute("""UPDATE daily_strategy_live_broker_cost_snapshot
                                   SET finalization_status=%s,updated_at=CURRENT_TIMESTAMP
                                 WHERE trade_date=%s AND execution_stock_code=%s""",
                               (status.value, snapshot.trade_date, snapshot.execution_stock_code))
                if self.commit: connection.commit()
                return status
            if row is None:
                cursor.execute("""INSERT INTO daily_strategy_live_broker_cost_snapshot
                              (broker_cost_snapshot_id,trade_date,execution_stock_code,broker_buy_fee,broker_sell_fee,broker_sell_tax,
                               broker_other_cost,broker_snapshot_at,finalization_status,finalized_at)
                              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (snapshot_id, snapshot.trade_date, snapshot.execution_stock_code, snapshot.totals.buy_fee,
                     snapshot.totals.sell_fee, snapshot.totals.sell_tax, snapshot.totals.other_cost,
                     snapshot.broker_snapshot_at, status.value, snapshot.broker_snapshot_at if snapshot.final else None))
            if status is not BrokerCostStatus.FINALIZED:
                if self.commit: connection.commit()
                return status
            allocation_status, allocations = allocate_final_costs(snapshot=snapshot, targets=targets,
                                                                   unattributed_activity=unattributed_activity)
            if allocation_status is not BrokerCostStatus.FINALIZED:
                cursor.execute("""UPDATE daily_strategy_live_broker_cost_snapshot
                                   SET finalization_status=%s,updated_at=CURRENT_TIMESTAMP
                                 WHERE broker_cost_snapshot_id=%s""", (allocation_status.value, snapshot_id))
                if self.commit: connection.commit()
                return allocation_status
            for allocation in allocations:
                stable_key = f"{allocation.live_trade_id}|{allocation.side}"
                cursor.execute("""INSERT INTO daily_strategy_live_broker_cost_allocation
                                  (broker_cost_snapshot_id,live_trade_id,allocation_side,fill_notional,allocated_buy_fee,
                                   allocated_sell_fee,allocated_sell_tax,allocated_other_cost,stable_allocation_key)
                                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                                  ON CONFLICT (broker_cost_snapshot_id,live_trade_id,allocation_side) DO NOTHING""",
                    (snapshot_id, allocation.live_trade_id, allocation.side, allocation.fill_notional,
                     allocation.buy_fee, allocation.sell_fee, allocation.sell_tax, allocation.other_cost, stable_key))
            cursor.execute("""UPDATE daily_strategy_live_broker_cost_snapshot
                               SET buy_fill_notional_denominator=%s,sell_fill_notional_denominator=%s,
                                   finalization_status='FINALIZED',finalized_at=%s,updated_at=CURRENT_TIMESTAMP
                             WHERE broker_cost_snapshot_id=%s""",
                (sum((a.fill_notional for a in allocations if a.side == 'BUY'), 0),
                 sum((a.fill_notional for a in allocations if a.side == 'SELL'), 0),
                 snapshot.broker_snapshot_at, snapshot_id))
            if self.commit: connection.commit()
        return BrokerCostStatus.FINALIZED
