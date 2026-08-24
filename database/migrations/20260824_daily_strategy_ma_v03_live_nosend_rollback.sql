-- DRAFT ONLY. Guarded rollback: never deletes/re-writes any historical LIVE,
-- broker, fill, ownership, or capital row.
BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM daily_strategy_live_order_intent)
       OR EXISTS (SELECT 1 FROM daily_strategy_live_order_request)
       OR EXISTS (SELECT 1 FROM daily_strategy_live_broker_order_mapping)
       OR EXISTS (SELECT 1 FROM daily_strategy_live_capital_reservation)
       OR EXISTS (SELECT 1 FROM daily_strategy_live_trade WHERE live_trade_key IS NOT NULL) THEN
        RAISE EXCEPTION 'LIVE NO_SEND/V0.3 rows exist; schema rollback is blocked';
    END IF;
END $$;

DROP TABLE IF EXISTS daily_strategy_live_capital_reservation;
DROP TABLE IF EXISTS daily_strategy_live_broker_order_mapping;
DROP TABLE IF EXISTS daily_strategy_live_order_request;
DROP TABLE IF EXISTS daily_strategy_live_order_intent;
DROP INDEX IF EXISTS ix_daily_strategy_live_intent_paper;
DROP INDEX IF EXISTS ix_daily_strategy_live_intent_trade;
DROP INDEX IF EXISTS ux_daily_strategy_live_v03_ownership;
DROP INDEX IF EXISTS ux_daily_strategy_live_v03_trade_key;
ALTER TABLE daily_strategy_live_trade DROP CONSTRAINT IF EXISTS ck_daily_strategy_live_v03_lifecycle;
ALTER TABLE daily_strategy_live_trade
    DROP COLUMN IF EXISTS exit_intent_key,
    DROP COLUMN IF EXISTS entry_intent_key,
    DROP COLUMN IF EXISTS lifecycle_status,
    DROP COLUMN IF EXISTS ownership_id,
    DROP COLUMN IF EXISTS live_trade_key;
COMMIT;
