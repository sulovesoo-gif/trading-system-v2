-- READ ONLY. Required before considering the Daily MA V0.3 LIVE NO_SEND migration.
-- This audit does not use the current zero-row condition as a migration prerequisite.

SELECT column_name, data_type, is_nullable, column_default
  FROM information_schema.columns
 WHERE table_schema='public' AND table_name='daily_strategy_live_trade'
 ORDER BY ordinal_position;

SELECT conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conrelid='daily_strategy_live_trade'::regclass
 ORDER BY conname;

SELECT indexname, indexdef
  FROM pg_indexes
 WHERE schemaname='public' AND tablename='daily_strategy_live_trade'
 ORDER BY indexname;

-- The official exposure output contract must not change. The additive
-- migration does not replace this view; this captures its pre-apply shape.
SELECT column_name, ordinal_position, data_type, is_nullable
  FROM information_schema.columns
 WHERE table_schema='public' AND table_name='vw_daily_strategy_live_exposure'
 ORDER BY ordinal_position;

SELECT pg_get_viewdef('vw_daily_strategy_live_exposure'::regclass, TRUE)
       AS live_exposure_definition;

-- Existing DB consumers: views/functions dependent on the historical table.
SELECT dependent.relkind, dependent.relname
  FROM pg_depend d
  JOIN pg_rewrite r ON r.oid=d.objid
  JOIN pg_class dependent ON dependent.oid=r.ev_class
 WHERE d.refobjid='daily_strategy_live_trade'::regclass
 ORDER BY dependent.relkind, dependent.relname;

SELECT to_regclass('public.live_strategy_intent') AS live_intent_table,
       to_regclass('public.live_broker_order') AS live_broker_order_table,
       to_regclass('public.live_broker_fill') AS live_broker_fill_table,
       to_regclass('public.execution_fill_allocation') AS fill_allocation_table,
       to_regclass('public.execution_logical_position') AS logical_position_table;

-- `live_broker_order.status` is intentionally inspected, not altered.
SELECT conname, pg_get_constraintdef(oid) AS definition
  FROM pg_constraint
 WHERE conrelid=to_regclass('public.live_broker_order')
 ORDER BY conname;

SELECT count(*) AS existing_live_trade_rows FROM daily_strategy_live_trade;

-- Broker ledger counts are intentionally collected by the post-apply verifier.
-- These prerequisite objects may not be installed in every pre-apply database.
SELECT attr1 AS global_trade_yn
  FROM common_code WHERE group_cd='SYSTEM_SWITCH' AND code='GLOBAL_TRADE_YN';
