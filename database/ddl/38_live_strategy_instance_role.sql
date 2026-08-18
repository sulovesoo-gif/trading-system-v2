-- Additive identity classification; legacy rows remain immutable history.
ALTER TABLE research_live_strategy
    ADD COLUMN IF NOT EXISTS instance_role VARCHAR(40) NOT NULL DEFAULT 'LEGACY_SMOKE_TRANSITIONAL';
ALTER TABLE research_live_strategy
    DROP CONSTRAINT IF EXISTS research_live_strategy_instance_role_check;
ALTER TABLE research_live_strategy
    ADD CONSTRAINT research_live_strategy_instance_role_check
    CHECK (instance_role IN ('CANONICAL_LIVE','LEGACY_SMOKE_TRANSITIONAL'));
CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_live_strategy
    ON research_live_strategy(strategy_id) WHERE instance_role='CANONICAL_LIVE';
