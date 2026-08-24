-- Read-only V0.4.2 verification.  Empty ledgers are expected before SEND.
SELECT to_regclass('public.daily_strategy_live_broker_cost_snapshot') IS NOT NULL AS snapshot_table_exists,
       to_regclass('public.daily_strategy_live_broker_cost_allocation') IS NOT NULL AS allocation_table_exists;

SELECT count(*) AS duplicate_snapshot_keys FROM (
  SELECT trade_date,execution_stock_code FROM daily_strategy_live_broker_cost_snapshot
  GROUP BY trade_date,execution_stock_code HAVING count(*) > 1
) d;

SELECT count(*) AS allocation_total_mismatch FROM (
  SELECT s.broker_cost_snapshot_id
    FROM daily_strategy_live_broker_cost_snapshot s
    LEFT JOIN daily_strategy_live_broker_cost_allocation a USING(broker_cost_snapshot_id)
   WHERE s.finalization_status='FINALIZED_BY_STABLE_RECHECK'
   GROUP BY s.broker_cost_snapshot_id,s.broker_buy_fee,s.broker_sell_fee,s.broker_sell_tax,s.broker_other_cost
  HAVING COALESCE(sum(a.allocated_buy_fee),0) <> s.broker_buy_fee
      OR COALESCE(sum(a.allocated_sell_fee),0) <> s.broker_sell_fee
      OR COALESCE(sum(a.allocated_sell_tax),0) <> s.broker_sell_tax
      OR COALESCE(sum(a.allocated_other_cost),0) <> s.broker_other_cost
) d;

SELECT count(*) AS pending_cost_snapshots
  FROM daily_strategy_live_broker_cost_snapshot WHERE finalization_status='PENDING_BROKER_COST';
