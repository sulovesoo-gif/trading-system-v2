-- V0.4.2 deterministic T+1 stable-recheck finalization policy.
BEGIN;
ALTER TABLE daily_strategy_live_broker_cost_snapshot
  ADD COLUMN IF NOT EXISTS stable_confirmation_count INTEGER NOT NULL DEFAULT 0 CHECK (stable_confirmation_count >= 0),
  ADD COLUMN IF NOT EXISTS fill_set_fingerprint CHAR(64) NULL,
  ADD COLUMN IF NOT EXISTS last_stable_recheck_at TIMESTAMP NULL;

ALTER TABLE daily_strategy_live_broker_cost_snapshot
  DROP CONSTRAINT IF EXISTS daily_strategy_live_broker_cost_snapshot_finalization_status_check;
ALTER TABLE daily_strategy_live_broker_cost_snapshot
  ADD CONSTRAINT ck_daily_ma_broker_cost_finalization_status CHECK (finalization_status IN (
    'PENDING_BROKER_COST','FINALIZED_BY_STABLE_RECHECK','BROKER_COST_ATTRIBUTION_BLOCKED','BROKER_COST_SNAPSHOT_REGRESSION'
  ));
COMMIT;
