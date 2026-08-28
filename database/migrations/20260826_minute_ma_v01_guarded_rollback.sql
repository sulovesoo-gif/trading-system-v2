-- Guarded rollback: never remove a layer that contains approved selection or runtime history.
BEGIN;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM minute_ma_selection_batch WHERE status='APPROVED')
     OR EXISTS (SELECT 1 FROM minute_ma_paper_event)
     OR EXISTS (SELECT 1 FROM minute_ma_paper_trade)
     OR EXISTS (SELECT 1 FROM minute_ma_live_trade)
     OR EXISTS (SELECT 1 FROM minute_ma_live_intent) THEN
    RAISE EXCEPTION 'minute MA history exists; guarded rollback refused';
  END IF;
END $$;
DROP VIEW IF EXISTS vw_minute_ma_dashboard;
DROP VIEW IF EXISTS vw_minute_ma_current_selection;
DROP TABLE IF EXISTS minute_ma_live_order_link;
DROP TABLE IF EXISTS minute_ma_live_capital_reservation;
DROP TABLE IF EXISTS minute_ma_live_entry_skip;
DROP TABLE IF EXISTS minute_ma_live_intent;
DROP TABLE IF EXISTS minute_ma_live_capital_settlement;
DROP TABLE IF EXISTS minute_ma_live_trade;
DROP TABLE IF EXISTS minute_ma_compound_capital;
DROP TABLE IF EXISTS minute_ma_operation;
DROP TRIGGER IF EXISTS trg_minute_ma_selection_snapshot_immutable ON minute_ma_selection_snapshot;
DROP FUNCTION IF EXISTS fn_minute_ma_selection_snapshot_immutable();
DROP TABLE IF EXISTS minute_ma_selection_snapshot;
DROP TABLE IF EXISTS minute_ma_selection_batch;
DROP TABLE IF EXISTS minute_ma_runtime_cursor;
DROP TABLE IF EXISTS minute_ma_paper_settlement;
DROP TABLE IF EXISTS minute_ma_paper_capital;
DROP TABLE IF EXISTS minute_ma_paper_trade;
DROP TABLE IF EXISTS minute_ma_paper_event;
DROP TABLE IF EXISTS minute_ma_send_profile;
DROP TABLE IF EXISTS minute_ma_path;
DROP TABLE IF EXISTS minute_ma_strategy_master;
COMMIT;
