-- DRAFT ONLY. Never execute after any broker ledger row or dependent mapping
-- exists. This is a TEST prerequisite rollback, not a history-rewrite tool.
BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM live_broker_order)
       OR EXISTS (SELECT 1 FROM live_broker_fill)
       OR EXISTS (SELECT 1 FROM live_broker_order_audit)
       OR to_regclass('public.daily_strategy_live_broker_order_mapping') IS NOT NULL THEN
        RAISE EXCEPTION 'DDL 31 rollback blocked by broker data or V0.3 mapping dependency';
    END IF;
END $$;

DROP TABLE live_broker_order_audit;
DROP TABLE live_broker_fill;
DROP TABLE live_broker_order;
COMMIT;
