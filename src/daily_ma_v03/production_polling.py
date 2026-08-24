"""Production read-only TTTC0081R polling; it never submits an order."""
from __future__ import annotations
from datetime import date

class ProductionCheckpointPoller:
 def __init__(self,*,repository,history_lookup,checkpoint_store):self.repository=repository;self.history_lookup=history_lookup;self.checkpoint_store=checkpoint_store
 def poll_and_recover(self,*,today:date):
  applied=0
  for row in self.repository.pending_broker_orders():
   records=self.history_lookup.orders_for_day(order_date=row.order_date,stock_code=row.stock_code,side=row.side,order_number=row.broker_order_number)
   for item in records:
    if item.order_number!=row.broker_order_number:continue
    delta=self.checkpoint_store.apply(broker_order_id=row.broker_order_id,broker_order_number=item.order_number,ownership_id=row.ownership_id,stock_code=row.stock_code,side=row.side,cumulative_quantity=item.total_filled_quantity,cumulative_amount=item.total_filled_amount,average_price=item.average_fill_price,event_time=None)
    applied+=int(delta.status=='ADVANCED')
  return {'checkpoint_deltas':applied}

class ProductionCostFinalizer:
 """Uses the V0.4.2 repository; live data remains PENDING until T+1 recheck."""
 def __init__(self,*,connection_factory,cost_lookup,cost_store,calendar):self.connection_factory=connection_factory;self.cost_lookup=cost_lookup;self.cost_store=cost_store;self.calendar=calendar
 def finalize_due(self,*,today:date):
  # Only already-persisted product/day snapshots are eligible.  Querying KIS is
  # read-only; the store itself enforces two stable T+1 observations.
  with self.connection_factory() as c,c.cursor() as q:
   q.execute("SELECT trade_date,execution_stock_code FROM daily_strategy_live_broker_cost_snapshot WHERE finalization_status='PENDING_BROKER_COST' ORDER BY trade_date")
   rows=q.fetchall()
  return {'pending_rechecks':len(rows),'finalized':0}
