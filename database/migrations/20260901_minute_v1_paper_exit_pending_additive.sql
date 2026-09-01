-- Durable late-proxy lifecycle for Minute V1 PAPER NORMAL/STOP exits.
-- Additive only: this migration deliberately performs no historical backfill.
BEGIN;

DO $$ BEGIN
  IF to_regclass('public.minute_ma_policy_path') IS NULL
     OR to_regclass('public.minute_ma_policy_paper_trade') IS NULL
     OR to_regclass('public.minute_ma_policy_paper_event') IS NULL THEN
    RAISE EXCEPTION 'Minute V1 PAPER exit-pending prerequisites are missing';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS minute_ma_policy_paper_pending_exit (
  pending_exit_id BIGSERIAL PRIMARY KEY,
  minute_policy_path_id BIGINT NOT NULL
    REFERENCES minute_ma_policy_path(minute_policy_path_id),
  signal_event_key CHAR(64) NOT NULL,
  exit_type VARCHAR(16) NOT NULL CHECK (exit_type IN ('NORMAL_EXIT','STOP_EXIT')),
  target_paper_trade_id BIGINT
    REFERENCES minute_ma_policy_paper_trade(minute_policy_paper_trade_id),
  source_bar_time TIMESTAMP NOT NULL,
  confirmed_at TIMESTAMP NOT NULL,
  proxy_bar_time TIMESTAMP NOT NULL,
  trigger_underlying_close NUMERIC(20,6),
  source_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  pending_reason VARCHAR(48) NOT NULL
    CHECK (pending_reason='EXECUTION_PROXY_MISSING'),
  pending_status VARCHAR(16) NOT NULL DEFAULT 'PENDING'
    CHECK (pending_status IN ('PENDING','COMPLETED')),
  signal_source VARCHAR(40) NOT NULL DEFAULT 'KIS_H0STCNT0_REALTIME',
  source_bar_finalized_at TIMESTAMP,
  evaluated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  first_pending_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at TIMESTAMP,
  UNIQUE(minute_policy_path_id,signal_event_key,exit_type),
  CHECK ((exit_type='NORMAL_EXIT' AND target_paper_trade_id IS NULL
                              AND trigger_underlying_close IS NULL)
      OR (exit_type='STOP_EXIT' AND target_paper_trade_id IS NOT NULL
                              AND trigger_underlying_close IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS ix_minute_ma_policy_pending_exit_open
  ON minute_ma_policy_paper_pending_exit(proxy_bar_time,minute_policy_path_id)
  WHERE pending_status='PENDING';

CREATE UNIQUE INDEX IF NOT EXISTS ux_minute_ma_policy_pending_stop_trade
  ON minute_ma_policy_paper_pending_exit(target_paper_trade_id)
  WHERE exit_type='STOP_EXIT' AND pending_status='PENDING';

COMMENT ON TABLE minute_ma_policy_paper_pending_exit IS
  'Durable V1 PAPER NORMAL/STOP exit signals waiting for the strict next-minute execution OPEN; no historical backfill';

COMMIT;
