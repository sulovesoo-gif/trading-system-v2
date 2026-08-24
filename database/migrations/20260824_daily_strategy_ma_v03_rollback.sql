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
-- Restore the official views from their independent history definitions before
-- dropping the history copies. No view is renamed or dropped during Migration A.
DO $$
DECLARE
    source_view TEXT;
    history_view TEXT;
    definition_sql TEXT;
BEGIN
    FOREACH source_view IN ARRAY ARRAY[
        'vw_daily_strategy_current_operation',
        'vw_daily_strategy_performance_all',
        'vw_daily_strategy_recent10',
        'vw_daily_strategy_live_exposure',
        'vw_daily_strategy_paper_live_audit',
        'vw_daily_strategy_latest_dominance'
    ]
    LOOP
        history_view := source_view || '_history';
        IF to_regclass('public.' || history_view) IS NOT NULL THEN
            SELECT pg_get_viewdef(('public.' || history_view)::regclass, TRUE)
              INTO definition_sql;
            EXECUTE format('CREATE OR REPLACE VIEW public.%I AS %s', source_view, definition_sql);
        END IF;
    END LOOP;

    IF to_regclass('public.vw_daily_strategy_dashboard_history') IS NOT NULL THEN
        SELECT pg_get_viewdef('public.vw_daily_strategy_dashboard_history'::regclass, TRUE)
          INTO definition_sql;
        definition_sql := replace(
            definition_sql,
            'vw_daily_strategy_current_operation_history',
            'vw_daily_strategy_current_operation');
        definition_sql := replace(
            definition_sql,
            'vw_daily_strategy_performance_all_history',
            'vw_daily_strategy_performance_all');
        definition_sql := replace(
            definition_sql,
            'vw_daily_strategy_recent10_history',
            'vw_daily_strategy_recent10');
        EXECUTE format('CREATE OR REPLACE VIEW public.%I AS %s', 'vw_daily_strategy_dashboard', definition_sql);
    END IF;
END $$;

DROP VIEW IF EXISTS vw_daily_strategy_dashboard_history;
DROP VIEW IF EXISTS vw_daily_strategy_latest_dominance_history;
DROP VIEW IF EXISTS vw_daily_strategy_paper_live_audit_history;
DROP VIEW IF EXISTS vw_daily_strategy_live_exposure_history;
DROP VIEW IF EXISTS vw_daily_strategy_recent10_history;
DROP VIEW IF EXISTS vw_daily_strategy_performance_all_history;
DROP VIEW IF EXISTS vw_daily_strategy_current_operation_history;
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
