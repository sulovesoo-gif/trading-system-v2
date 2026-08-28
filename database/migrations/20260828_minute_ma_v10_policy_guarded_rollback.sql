-- Destructive rollback is permitted only before any V1 durable operating data
-- exists. Candidate-plan and initial PAPER-capital rows are schema seed only.
BEGIN;

DO $$
DECLARE blockers bigint;
BEGIN
  SELECT
    (SELECT count(*) FROM minute_ma_policy_paper_event) +
    (SELECT count(*) FROM minute_ma_policy_paper_trade) +
    (SELECT count(*) FROM minute_ma_policy_paper_settlement) +
    (SELECT count(*) FROM minute_ma_policy_runtime_cursor) +
    (SELECT count(*) FROM minute_ma_policy_operation) +
    (SELECT count(*) FROM minute_ma_policy_compound_capital) +
    (SELECT count(*) FROM minute_ma_v1_daily_telemetry_snapshot) +
    (SELECT count(*) FROM minute_ma_selection_batch
      WHERE selection_scope='MINUTE_MA_V1_OPERATION') +
    (SELECT count(*) FROM minute_ma_live_signal_event
      WHERE minute_policy_path_id IS NOT NULL) +
    (SELECT count(*) FROM minute_ma_live_trade
      WHERE minute_policy_path_id IS NOT NULL) +
    (SELECT count(*) FROM minute_ma_live_intent
      WHERE minute_policy_path_id IS NOT NULL) +
    (SELECT count(*) FROM minute_ma_live_entry_skip
      WHERE minute_policy_path_id IS NOT NULL) +
    (SELECT count(*) FROM minute_ma_live_capital_settlement
      WHERE minute_policy_path_id IS NOT NULL) +
    (SELECT count(*) FROM minute_ma_live_order_link l
      JOIN minute_ma_live_intent i USING(intent_id)
      WHERE i.minute_policy_path_id IS NOT NULL) +
    (SELECT count(*) FROM minute_ma_live_fill_checkpoint c
      JOIN minute_ma_live_order_link l USING(broker_order_id)
      JOIN minute_ma_live_intent i USING(intent_id)
      WHERE i.minute_policy_path_id IS NOT NULL) +
    (SELECT count(*) FROM minute_ma_live_checkpoint_allocation a
      JOIN minute_ma_live_trade t USING(minute_live_trade_id)
      WHERE t.minute_policy_path_id IS NOT NULL) +
    (SELECT count(*) FROM minute_ma_live_broker_cost_allocation a
      JOIN minute_ma_live_trade t USING(minute_live_trade_id)
      WHERE t.minute_policy_path_id IS NOT NULL) +
    (SELECT count(*) FROM execution_logical_position p
      JOIN minute_ma_live_trade t
        ON p.ownership_type='MINUTE_MA' AND p.ownership_id=t.ownership_id
      WHERE t.minute_policy_path_id IS NOT NULL)
  INTO blockers;
  IF blockers<>0 THEN
    RAISE EXCEPTION 'rollback blocked: % durable V1 operating references exist',blockers;
  END IF;
END $$;

ALTER TABLE minute_ma_live_trade DROP CONSTRAINT IF EXISTS ck_minute_ma_live_trade_operation_identity;
DROP VIEW IF EXISTS vw_minute_ma_v1_policy_dashboard;
DROP VIEW IF EXISTS vw_minute_ma_v1_current_selection;
DROP VIEW IF EXISTS vw_minute_ma_current_selection_scoped;
DROP VIEW IF EXISTS vw_minute_ma_current_selection;

DROP INDEX IF EXISTS ux_minute_ma_v1_stop_intent;
ALTER TABLE minute_ma_live_capital_settlement DROP COLUMN IF EXISTS minute_policy_operation_id;
ALTER TABLE minute_ma_live_capital_settlement DROP COLUMN IF EXISTS minute_policy_path_id;
ALTER TABLE minute_ma_live_entry_skip DROP COLUMN IF EXISTS minute_policy_operation_id;
ALTER TABLE minute_ma_live_entry_skip DROP COLUMN IF EXISTS minute_policy_path_id;
ALTER TABLE minute_ma_live_intent DROP COLUMN IF EXISTS exit_reason;
ALTER TABLE minute_ma_live_intent DROP COLUMN IF EXISTS stop_policy;
ALTER TABLE minute_ma_live_intent DROP COLUMN IF EXISTS stop_threshold_price;
ALTER TABLE minute_ma_live_intent DROP COLUMN IF EXISTS underlying_entry_reference_price;
ALTER TABLE minute_ma_live_intent DROP COLUMN IF EXISTS target_minute_live_trade_id;
ALTER TABLE minute_ma_live_intent DROP COLUMN IF EXISTS minute_policy_operation_id;
ALTER TABLE minute_ma_live_intent DROP COLUMN IF EXISTS minute_policy_path_id;
ALTER TABLE minute_ma_live_trade DROP COLUMN IF EXISTS stop_trigger_underlying_close;
ALTER TABLE minute_ma_live_trade DROP COLUMN IF EXISTS stop_trigger_time;
ALTER TABLE minute_ma_live_trade DROP COLUMN IF EXISTS stop_policy;
ALTER TABLE minute_ma_live_trade DROP COLUMN IF EXISTS stop_threshold_price;
ALTER TABLE minute_ma_live_trade DROP COLUMN IF EXISTS underlying_entry_reference_price;
ALTER TABLE minute_ma_live_trade DROP COLUMN IF EXISTS minute_policy_operation_id;
ALTER TABLE minute_ma_live_trade DROP COLUMN IF EXISTS minute_policy_path_id;
ALTER TABLE minute_ma_live_trade ALTER COLUMN operation_id SET NOT NULL;
ALTER TABLE minute_ma_live_signal_event DROP COLUMN IF EXISTS event_reason;
ALTER TABLE minute_ma_live_signal_event DROP COLUMN IF EXISTS minute_policy_path_id;

DROP INDEX IF EXISTS ux_minute_ma_selection_policy_snapshot;
ALTER TABLE minute_ma_selection_snapshot DROP COLUMN IF EXISTS source_daily_strategy_id;
ALTER TABLE minute_ma_selection_snapshot DROP COLUMN IF EXISTS minute_policy_path_id;
DROP INDEX IF EXISTS ix_minute_ma_selection_scope;
ALTER TABLE minute_ma_selection_batch DROP COLUMN IF EXISTS selection_purpose;
ALTER TABLE minute_ma_selection_batch DROP COLUMN IF EXISTS policy_version;
ALTER TABLE minute_ma_selection_batch DROP COLUMN IF EXISTS selection_scope;

CREATE VIEW vw_minute_ma_current_selection AS
WITH b AS (
  SELECT selection_batch_id FROM minute_ma_selection_batch WHERE status='APPROVED'
  ORDER BY selected_at DESC,selection_batch_id DESC LIMIT 1
)
SELECT s.* FROM minute_ma_selection_snapshot s JOIN b USING(selection_batch_id);

DROP TABLE IF EXISTS minute_ma_v1_daily_telemetry_snapshot;
DROP TABLE IF EXISTS minute_ma_v1_candidate_plan;
DROP TABLE IF EXISTS minute_ma_policy_compound_capital;
DROP TABLE IF EXISTS minute_ma_policy_operation;
DROP TABLE IF EXISTS minute_ma_policy_paper_settlement;
DROP TABLE IF EXISTS minute_ma_policy_paper_capital;
DROP TABLE IF EXISTS minute_ma_policy_paper_trade;
DROP TABLE IF EXISTS minute_ma_policy_paper_event;
DROP TABLE IF EXISTS minute_ma_policy_runtime_cursor;
DROP TABLE IF EXISTS minute_ma_policy_path;
DROP TABLE IF EXISTS minute_ma_operation_policy;
COMMIT;
