-- Durable strategy-instance attribution contract. Do not apply to production
-- without a separate registry-registration approval.
--
-- research_live_strategy already owns the durable BIGINT PK. Runtime IDs are
-- derived in code as LIVE_STRATEGY_<live_strategy_id>, not stored or random.

CREATE UNIQUE INDEX IF NOT EXISTS uq_research_live_strategy_live_name
    ON research_live_strategy (live_name);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_research_live_strategy_initial_capital_positive'
          AND conrelid = 'research_live_strategy'::regclass
    ) THEN
        ALTER TABLE research_live_strategy
            ADD CONSTRAINT chk_research_live_strategy_initial_capital_positive
            CHECK (initial_live_capital > 0);
    END IF;
END $$;
