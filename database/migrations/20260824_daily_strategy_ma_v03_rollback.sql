-- Roll back only V0.3 schema/role metadata.  Never deletes historical data.
BEGIN;

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM daily_strategy_paper_trade WHERE source_system = 'DAILY_MA_V03'
    ) THEN
        RAISE EXCEPTION 'V0.3 runtime rows exist; use a data-aware rollback plan instead of schema rollback';
    END IF;
END $$;

UPDATE daily_strategy_master SET is_enabled = 'Y' WHERE brake_type = 'NONE';
DROP VIEW IF EXISTS vw_daily_strategy_v03_legacy_brake_comparison;
DROP VIEW IF EXISTS vw_daily_strategy_v03_runtime;
DROP INDEX IF EXISTS ux_daily_strategy_master_v03_canonical_identity;
ALTER TABLE daily_strategy_paper_trade DROP CONSTRAINT IF EXISTS ck_daily_strategy_paper_v03_normal_tracking;
ALTER TABLE daily_strategy_master DROP CONSTRAINT IF EXISTS ck_daily_strategy_master_v03_role;
ALTER TABLE daily_strategy_paper_trade
    DROP COLUMN IF EXISTS day20_enabled_at_entry,
    DROP COLUMN IF EXISTS day20_applied,
    DROP COLUMN IF EXISTS day20_exit_time,
    DROP COLUMN IF EXISTS day20_exit_price,
    DROP COLUMN IF EXISTS normal_tracking_status,
    DROP COLUMN IF EXISTS normal_exit_time,
    DROP COLUMN IF EXISTS normal_exit_price,
    DROP COLUMN IF EXISTS normal_return_pct,
    DROP COLUMN IF EXISTS normal_fixed_basis_pnl,
    DROP COLUMN IF EXISTS day20_delta_return_pct,
    DROP COLUMN IF EXISTS day20_delta_fixed_basis_pnl;
ALTER TABLE daily_strategy_master
    DROP COLUMN IF EXISTS strategy_role,
    DROP COLUMN IF EXISTS day20_enabled;

COMMIT;
