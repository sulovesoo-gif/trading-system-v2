-- Daily MA parallel strategy V0.3 additive migration.
-- No strategy_id, historical PAPER/LIVE row, brake_type, or existing index is removed.
BEGIN;

-- Guard the documented 2,400 / 2,400 source population before changing roles.
-- A different population is a provenance mismatch, not a migration input to guess at.
DO $$
DECLARE
    day20_count BIGINT;
    none_count BIGINT;
    other_count BIGINT;
BEGIN
    SELECT count(*) FILTER (WHERE brake_type = 'DAY20'),
           count(*) FILTER (WHERE brake_type = 'NONE'),
           count(*) FILTER (WHERE brake_type NOT IN ('DAY20', 'NONE'))
      INTO day20_count, none_count, other_count
      FROM daily_strategy_master;

    IF day20_count <> 2400 OR none_count <> 2400 OR other_count <> 0 THEN
        RAISE EXCEPTION
            'V0.3 master provenance mismatch: DAY20=%, NONE=%, other=%',
            day20_count, none_count, other_count;
    END IF;
END $$;

ALTER TABLE daily_strategy_master
    ADD COLUMN IF NOT EXISTS strategy_role VARCHAR(32) NOT NULL DEFAULT 'LEGACY_BRAKE_COMPARISON',
    ADD COLUMN IF NOT EXISTS day20_enabled BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE daily_strategy_master
   SET strategy_role = CASE brake_type
       WHEN 'DAY20' THEN 'CANONICAL'
       WHEN 'NONE' THEN 'LEGACY_BRAKE_COMPARISON'
       ELSE strategy_role
   END,
       day20_enabled = (brake_type = 'DAY20');

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_daily_strategy_master_v03_role') THEN
        ALTER TABLE daily_strategy_master
            ADD CONSTRAINT ck_daily_strategy_master_v03_role
            CHECK (strategy_role IN ('CANONICAL', 'LEGACY_BRAKE_COMPARISON'));
    END IF;
END $$;

-- V0.3 enables only the DAY20-side canonical lineage for new runtime work.
UPDATE daily_strategy_master
   SET is_enabled = CASE WHEN strategy_role = 'CANONICAL' THEN 'Y' ELSE 'N' END;

CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_strategy_master_v03_canonical_identity
    ON daily_strategy_master (
        signal_code, execution_code, direction,
        entry_fast_ma, entry_slow_ma, exit_fast_ma, exit_slow_ma,
        COALESCE(trend_ma, 0)
    )
    WHERE strategy_role = 'CANONICAL' AND is_enabled = 'Y';

-- The existing brake_type-bearing identity index remains intact for the
-- historical dual lineage. This partial index protects only V0.3's six axes.

ALTER TABLE daily_strategy_paper_trade
    ADD COLUMN IF NOT EXISTS day20_enabled_at_entry BOOLEAN,
    ADD COLUMN IF NOT EXISTS day20_applied BOOLEAN,
    ADD COLUMN IF NOT EXISTS day20_exit_time TIMESTAMP,
    ADD COLUMN IF NOT EXISTS day20_exit_price NUMERIC,
    ADD COLUMN IF NOT EXISTS normal_tracking_status VARCHAR(16),
    ADD COLUMN IF NOT EXISTS normal_exit_time TIMESTAMP,
    ADD COLUMN IF NOT EXISTS normal_exit_price NUMERIC,
    ADD COLUMN IF NOT EXISTS normal_return_pct NUMERIC,
    ADD COLUMN IF NOT EXISTS normal_fixed_basis_pnl NUMERIC,
    ADD COLUMN IF NOT EXISTS day20_delta_return_pct NUMERIC,
    ADD COLUMN IF NOT EXISTS day20_delta_fixed_basis_pnl NUMERIC;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_daily_strategy_paper_v03_normal_tracking') THEN
        ALTER TABLE daily_strategy_paper_trade
            ADD CONSTRAINT ck_daily_strategy_paper_v03_normal_tracking
            CHECK (normal_tracking_status IS NULL OR normal_tracking_status IN ('OPEN', 'CLOSED'));
    END IF;
END $$;

COMMENT ON COLUMN daily_strategy_master.strategy_role IS
    'V0.3 runtime role: DAY20 lineage is CANONICAL; NONE lineage is preserved comparison history.';
COMMENT ON COLUMN daily_strategy_master.day20_enabled IS
    'Risk-management setting, not strategy identity.';
COMMENT ON COLUMN daily_strategy_paper_trade.normal_tracking_status IS
    'DAY20 actual close may leave normal MA path OPEN until its later normal exit is observed.';

-- Historical/all-lineage views stay intact. New V0.3 runtime and official
-- V0.3 reporting must use the canonical-only views below. Existing operation
-- rows are preserved; NONE rows are excluded by the master role predicate.
CREATE OR REPLACE VIEW vw_daily_strategy_v03_runtime AS
SELECT m.strategy_id,
       m.strategy_name,
       m.signal_mode,
       m.signal_code,
       m.execution_code,
       m.direction,
       m.entry_fast_ma,
       m.entry_slow_ma,
       m.exit_fast_ma,
       m.exit_slow_ma,
       m.trend_ma,
       m.day20_enabled,
       o.operation_id,
       o.operation_status,
       o.allocated_amount,
       o.capital_epoch_no,
       o.effective_from
  FROM daily_strategy_master m
  JOIN daily_strategy_operation o
    ON o.strategy_id = m.strategy_id
   AND o.effective_to IS NULL
 WHERE m.strategy_role = 'CANONICAL'
   AND m.is_enabled = 'Y';

CREATE OR REPLACE VIEW vw_daily_strategy_v03_legacy_brake_comparison AS
SELECT m.strategy_id,
       m.strategy_name,
       m.signal_code,
       m.execution_code,
       m.direction,
       m.entry_fast_ma,
       m.entry_slow_ma,
       m.exit_fast_ma,
       m.exit_slow_ma,
       m.trend_ma,
       m.brake_type,
       m.is_enabled,
       o.operation_status,
       o.allocated_amount,
       o.effective_from
  FROM daily_strategy_master m
  LEFT JOIN daily_strategy_operation o
    ON o.strategy_id = m.strategy_id
   AND o.effective_to IS NULL
 WHERE m.strategy_role = 'LEGACY_BRAKE_COMPARISON';

COMMIT;
