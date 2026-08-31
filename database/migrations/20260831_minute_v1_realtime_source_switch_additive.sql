-- Minute V1 forward source authority: KIS H0STCNT0 realtime completed 1MIN.
BEGIN;

DO $$ BEGIN
  IF to_regclass('public.flow_realtime_minute_bar') IS NULL
     OR to_regclass('public.minute_ma_policy_path') IS NULL THEN
    RAISE EXCEPTION 'Realtime 1MIN/V1 prerequisites are missing';
  END IF;
END $$;

ALTER TABLE minute_ma_policy_paper_event
  ADD COLUMN IF NOT EXISTS signal_source VARCHAR(40) NOT NULL DEFAULT 'REST_1MIN_LEGACY',
  ADD COLUMN IF NOT EXISTS source_bar_finalized_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS evaluated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE minute_ma_live_signal_event
  ADD COLUMN IF NOT EXISTS signal_source VARCHAR(40) NOT NULL DEFAULT 'REST_1MIN_LEGACY',
  ADD COLUMN IF NOT EXISTS source_bar_finalized_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS evaluated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE minute_ma_policy_paper_pending_entry
  ADD COLUMN IF NOT EXISTS signal_source VARCHAR(40) NOT NULL DEFAULT 'KIS_H0STCNT0_REALTIME',
  ADD COLUMN IF NOT EXISTS source_bar_finalized_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS evaluated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS minute_ma_realtime_dispatch_cursor (
  consumer_code VARCHAR(32) PRIMARY KEY CHECK (consumer_code IN ('V1_PAPER','V1_LIVE')),
  last_finalized_at TIMESTAMP(6),
  last_bar_time TIMESTAMP,
  last_stock_code VARCHAR(20),
  last_success_at TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK ((last_finalized_at IS NULL AND last_bar_time IS NULL AND last_stock_code IS NULL)
      OR (last_finalized_at IS NOT NULL AND last_bar_time IS NOT NULL AND last_stock_code IS NOT NULL))
);
INSERT INTO minute_ma_realtime_dispatch_cursor(consumer_code)
VALUES('V1_PAPER'),('V1_LIVE') ON CONFLICT DO NOTHING;

COMMENT ON TABLE minute_ma_realtime_dispatch_cursor IS
  'Durable trigger cursor for realtime-finalized Minute V1 PAPER/LIVE evaluators';

COMMIT;
