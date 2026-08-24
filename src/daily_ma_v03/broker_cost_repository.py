"""Durable V0.4.2 broker-cost snapshots; settlement remains separately gated."""
from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from .broker_cost_allocation import (
    BrokerCostSnapshot, BrokerCostStatus, CostAllocationTarget, allocate_final_costs, classify_snapshot,
)
from .broker_cost_finalization import StableCostRecheck, stable_recheck


class PostgresDailyMaBrokerCostStore:
    """Idempotently persists final cost allocation; never creates broker fills."""
    def __init__(self, connection_factory, *, commit: bool = True) -> None:
        self.connection_factory, self.commit = connection_factory, commit

    def observe_stable_recheck(self, *, observed: BrokerCostSnapshot, fill_set_fingerprint: str,
                               unattributed_activity: bool, next_trade_date, minimum_interval):
        """Persist T+1 evidence so restart cannot accidentally count twice."""
        with self.connection_factory() as connection, connection.cursor() as cursor:
            cursor.execute("""SELECT broker_buy_fee,broker_sell_fee,broker_sell_tax,broker_other_cost,broker_snapshot_at,
                                     finalization_status,fill_set_fingerprint,stable_confirmation_count,last_stable_recheck_at
                                FROM daily_strategy_live_broker_cost_snapshot
                               WHERE trade_date=%s AND execution_stock_code=%s FOR UPDATE""",
                           (observed.trade_date, observed.execution_stock_code))
            row = cursor.fetchone()
            stored = None
            if row:
                from .broker_cost_allocation import BrokerCostTotals
                prior = BrokerCostSnapshot(observed.trade_date, observed.execution_stock_code, BrokerCostTotals(*row[:4]),
                    row[4], row[5] == BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK.value, BrokerCostStatus(row[5]))
                stored = StableCostRecheck(prior, row[6] or '', False, int(row[7]), row[8])
            state = stable_recheck(stored=stored, observed=observed, fill_set_fingerprint=fill_set_fingerprint,
                                  unattributed_activity=unattributed_activity, next_trade_date=next_trade_date,
                                  minimum_interval=minimum_interval)
            snapshot_id = str(uuid5(NAMESPACE_URL, f"daily-ma-v042-cost|{observed.trade_date}|{observed.execution_stock_code}"))
            cursor.execute("""INSERT INTO daily_strategy_live_broker_cost_snapshot
                              (broker_cost_snapshot_id,trade_date,execution_stock_code,broker_buy_fee,broker_sell_fee,broker_sell_tax,
                               broker_other_cost,broker_snapshot_at,finalization_status,stable_confirmation_count,
                               fill_set_fingerprint,last_stable_recheck_at,finalized_at)
                              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                              ON CONFLICT (trade_date,execution_stock_code) DO UPDATE SET
                               broker_buy_fee=EXCLUDED.broker_buy_fee,broker_sell_fee=EXCLUDED.broker_sell_fee,
                               broker_sell_tax=EXCLUDED.broker_sell_tax,broker_other_cost=EXCLUDED.broker_other_cost,
                               broker_snapshot_at=EXCLUDED.broker_snapshot_at,finalization_status=EXCLUDED.finalization_status,
                               stable_confirmation_count=EXCLUDED.stable_confirmation_count,
                               fill_set_fingerprint=EXCLUDED.fill_set_fingerprint,last_stable_recheck_at=EXCLUDED.last_stable_recheck_at,
                               finalized_at=EXCLUDED.finalized_at,updated_at=CURRENT_TIMESTAMP""",
                (snapshot_id, observed.trade_date, observed.execution_stock_code, state.snapshot.totals.buy_fee,
                 state.snapshot.totals.sell_fee, state.snapshot.totals.sell_tax, state.snapshot.totals.other_cost,
                 state.snapshot.broker_snapshot_at, state.snapshot.status.value, state.confirmation_count,
                 state.fill_set_fingerprint, state.last_confirmed_at,
                 state.snapshot.broker_snapshot_at if state.snapshot.final else None))
            if self.commit: connection.commit()
        return state

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
                    BrokerCostTotals(*row[1:5]), row[5], row[6] == BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK.value,
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
            if status is not BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK:
                if self.commit: connection.commit()
                return status
            allocation_status, allocations = allocate_final_costs(snapshot=snapshot, targets=targets,
                                                                   unattributed_activity=unattributed_activity)
            if allocation_status is not BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK:
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
                                   finalization_status='FINALIZED_BY_STABLE_RECHECK',finalized_at=%s,updated_at=CURRENT_TIMESTAMP
                             WHERE broker_cost_snapshot_id=%s""",
                (sum((a.fill_notional for a in allocations if a.side == 'BUY'), 0),
                 sum((a.fill_notional for a in allocations if a.side == 'SELL'), 0),
                 snapshot.broker_snapshot_at, snapshot_id))
            if self.commit: connection.commit()
        return BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK

    def closed_cost_finalized_trades(self):
        """Actual fill amounts are the CLOSED trade averages × quantities."""
        from types import SimpleNamespace
        from .broker_cost_allocation import CostAllocation
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("""SELECT t.live_trade_id,t.strategy_id,t.capital_epoch_no,
                              t.live_entry_avg_price*t.entry_quantity,t.live_exit_avg_price*t.exit_quantity,
                              a.allocation_side,a.fill_notional,a.allocated_buy_fee,a.allocated_sell_fee,a.allocated_sell_tax,a.allocated_other_cost
                         FROM daily_strategy_live_trade t
                         JOIN daily_strategy_live_broker_cost_allocation a USING(live_trade_id)
                         JOIN daily_strategy_live_broker_cost_snapshot s USING(broker_cost_snapshot_id)
                        WHERE t.trade_status='CLOSED' AND s.finalization_status='FINALIZED_BY_STABLE_RECHECK'
                          AND NOT EXISTS (SELECT 1 FROM daily_strategy_live_capital_settlement x WHERE x.live_trade_id=t.live_trade_id)
                        ORDER BY t.live_trade_id,a.allocation_side""")
            grouped={}
            for r in q.fetchall():
                item=grouped.setdefault(int(r[0]),[r[1],int(r[2]),r[3],r[4],[]])
                item[4].append(CostAllocation(int(r[0]),str(r[5]),r[6],r[7],r[8],r[9],r[10]))
            return tuple(SimpleNamespace(live_trade_id=k,strategy_id=str(v[0]),capital_epoch_no=v[1],entry_filled_amount=v[2],exit_filled_amount=v[3],allocations=tuple(v[4])) for k,v in grouped.items())
