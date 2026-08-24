BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM daily_strategy_compound_capital)
       OR EXISTS (SELECT 1 FROM daily_strategy_live_capital_settlement)
       OR EXISTS (SELECT 1 FROM daily_strategy_live_entry_skip) THEN
        RAISE EXCEPTION 'V0.4 capital data exists; guarded rollback is blocked';
    END IF;
END $$;

DROP TABLE IF EXISTS daily_strategy_live_entry_skip;
DROP TABLE IF EXISTS daily_strategy_live_capital_settlement;
DROP TABLE IF EXISTS daily_strategy_compound_capital;
ALTER TABLE daily_strategy_live_trade
    DROP COLUMN IF EXISTS capital_settled_at,
    DROP COLUMN IF EXISTS available_cash_at_signal,
    DROP COLUMN IF EXISTS strategy_compound_capital_at_signal;
ALTER TABLE daily_strategy_live_order_intent
    DROP COLUMN IF EXISTS cash_gate_checked_at,
    DROP COLUMN IF EXISTS available_cash_snapshot,
    DROP COLUMN IF EXISTS strategy_compound_capital_at_signal,
    DROP COLUMN IF EXISTS capital_epoch_no;
COMMIT;
