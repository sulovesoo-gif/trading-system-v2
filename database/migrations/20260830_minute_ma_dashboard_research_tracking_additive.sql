-- Minute V1 Historical research provenance. Never writes Forward PAPER or LIVE ledgers.
BEGIN;

CREATE TABLE IF NOT EXISTS minute_ma_policy_historical_run (
  historical_run_id UUID PRIMARY KEY,
  policy_version VARCHAR(32) NOT NULL,
  evaluation_from DATE NOT NULL,
  evaluation_to DATE NOT NULL,
  initial_virtual_capital NUMERIC NOT NULL DEFAULT 1000000
    CHECK (initial_virtual_capital=1000000),
  provenance VARCHAR(32) NOT NULL CHECK (provenance='HISTORICAL_REPLAY'),
  source_contract VARCHAR(128) NOT NULL,
  code_commit CHAR(40) NOT NULL,
  status VARCHAR(16) NOT NULL CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
  path_count INTEGER NOT NULL DEFAULT 0,
  trade_count BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP,
  CHECK (evaluation_to>=evaluation_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_minute_ma_v1_historical_completed_range
  ON minute_ma_policy_historical_run(policy_version,evaluation_from,evaluation_to)
  WHERE status='COMPLETED';

CREATE TABLE IF NOT EXISTS minute_ma_policy_historical_trade (
  minute_policy_historical_trade_id BIGSERIAL PRIMARY KEY,
  historical_run_id UUID NOT NULL REFERENCES minute_ma_policy_historical_run(historical_run_id),
  minute_policy_path_id BIGINT NOT NULL REFERENCES minute_ma_policy_path(minute_policy_path_id),
  entry_event_key CHAR(64) NOT NULL,
  entry_signal_time TIMESTAMP NOT NULL,
  entry_execution_time TIMESTAMP NOT NULL,
  entry_price NUMERIC NOT NULL CHECK (entry_price>0),
  underlying_entry_reference_price NUMERIC NOT NULL CHECK (underlying_entry_reference_price>0),
  stop_threshold_price NUMERIC NOT NULL CHECK (stop_threshold_price>0),
  exit_signal_time TIMESTAMP NOT NULL,
  exit_execution_time TIMESTAMP NOT NULL,
  exit_price NUMERIC NOT NULL CHECK (exit_price>0),
  exit_reason VARCHAR(24) NOT NULL CHECK (exit_reason IN ('NORMAL_EXIT','STOP_EXIT')),
  stop_trigger_time TIMESTAMP,
  stop_trigger_underlying_close NUMERIC,
  basis_capital NUMERIC NOT NULL CHECK (basis_capital>0),
  gross_return_pct NUMERIC NOT NULL,
  net_return_pct NUMERIC NOT NULL,
  realized_pnl NUMERIC NOT NULL,
  provenance VARCHAR(32) NOT NULL DEFAULT 'HISTORICAL_REPLAY'
    CHECK (provenance='HISTORICAL_REPLAY'),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(historical_run_id,minute_policy_path_id,entry_event_key)
);
CREATE INDEX IF NOT EXISTS ix_minute_ma_v1_historical_trade_period
  ON minute_ma_policy_historical_trade(minute_policy_path_id,exit_execution_time);
CREATE INDEX IF NOT EXISTS ix_minute_ma_v1_historical_trade_run
  ON minute_ma_policy_historical_trade(historical_run_id,minute_policy_path_id);

CREATE OR REPLACE VIEW vw_minute_ma_v1_current_historical_run AS
SELECT * FROM minute_ma_policy_historical_run
 WHERE status='COMPLETED'
 ORDER BY evaluation_to DESC,completed_at DESC,historical_run_id DESC
 LIMIT 1;

COMMIT;
