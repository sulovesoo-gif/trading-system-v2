-- Minute MA actual-send durable execution bookkeeping. Existing rows are preserved.
BEGIN;

CREATE TABLE IF NOT EXISTS minute_ma_live_signal_event (
  minute_live_signal_event_id UUID PRIMARY KEY,
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  signal_event_key CHAR(64) NOT NULL,
  event_type VARCHAR(8) NOT NULL CHECK (event_type IN ('ENTRY','EXIT')),
  source_bar_time TIMESTAMP NOT NULL,
  confirmed_at TIMESTAMP NOT NULL,
  source_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (minute_path_id,signal_event_key,event_type)
);

ALTER TABLE minute_ma_live_intent
  ALTER COLUMN minute_paper_trade_id DROP NOT NULL;
ALTER TABLE minute_ma_live_trade
  ALTER COLUMN minute_paper_trade_id DROP NOT NULL;
ALTER TABLE minute_ma_live_entry_skip
  ALTER COLUMN minute_paper_trade_id DROP NOT NULL;
ALTER TABLE minute_ma_live_intent
  ADD COLUMN IF NOT EXISTS minute_live_signal_event_id UUID NULL
    REFERENCES minute_ma_live_signal_event(minute_live_signal_event_id);

CREATE TABLE IF NOT EXISTS minute_ma_live_fill_checkpoint (
  broker_order_id UUID PRIMARY KEY REFERENCES live_broker_order(broker_order_id),
  broker_order_number VARCHAR(64) NOT NULL,
  cumulative_filled_qty INTEGER NOT NULL CHECK (cumulative_filled_qty>=0),
  cumulative_filled_amount NUMERIC(20,2) NOT NULL CHECK (cumulative_filled_amount>=0),
  last_avg_fill_price NUMERIC(20,6) NOT NULL CHECK (last_avg_fill_price>=0),
  last_broker_event_time TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 0 CHECK (version>=0),
  checkpoint_status VARCHAR(48) NOT NULL DEFAULT 'ACTIVE' CHECK (
    checkpoint_status IN ('ACTIVE','BROKER_FILL_CHECKPOINT_REGRESSION')),
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS minute_ma_live_checkpoint_allocation (
  allocation_id UUID PRIMARY KEY,
  broker_order_id UUID NOT NULL REFERENCES live_broker_order(broker_order_id),
  checkpoint_version INTEGER NOT NULL,
  minute_live_trade_id BIGINT NOT NULL REFERENCES minute_ma_live_trade(minute_live_trade_id),
  ownership_id VARCHAR(120) NOT NULL,
  stock_code VARCHAR(32) NOT NULL,
  side VARCHAR(8) NOT NULL CHECK (side IN ('BUY','SELL')),
  delta_quantity INTEGER NOT NULL CHECK (delta_quantity>0),
  delta_amount NUMERIC(20,2) NOT NULL CHECK (delta_amount>0),
  broker_event_time TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (broker_order_id,checkpoint_version)
);

CREATE TABLE IF NOT EXISTS minute_ma_live_broker_cost_snapshot (
  broker_cost_snapshot_id UUID PRIMARY KEY,
  trade_date DATE NOT NULL,
  execution_stock_code VARCHAR(32) NOT NULL,
  broker_buy_fee NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (broker_buy_fee>=0),
  broker_sell_fee NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (broker_sell_fee>=0),
  broker_sell_tax NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (broker_sell_tax>=0),
  broker_other_cost NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (broker_other_cost>=0),
  broker_snapshot_at TIMESTAMP NOT NULL,
  finalization_status VARCHAR(48) NOT NULL CHECK (finalization_status IN (
    'PENDING_BROKER_COST','FINALIZED_BY_STABLE_RECHECK','BROKER_COST_ATTRIBUTION_BLOCKED',
    'BROKER_COST_SNAPSHOT_REGRESSION')),
  stable_confirmation_count INTEGER NOT NULL DEFAULT 0,
  fill_set_fingerprint CHAR(64),
  last_stable_recheck_at TIMESTAMP,
  finalized_at TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(trade_date,execution_stock_code)
);

CREATE TABLE IF NOT EXISTS minute_ma_live_broker_cost_allocation (
  broker_cost_snapshot_id UUID NOT NULL REFERENCES minute_ma_live_broker_cost_snapshot(broker_cost_snapshot_id),
  minute_live_trade_id BIGINT NOT NULL REFERENCES minute_ma_live_trade(minute_live_trade_id),
  allocation_side VARCHAR(8) NOT NULL CHECK (allocation_side IN ('BUY','SELL')),
  fill_notional NUMERIC(20,2) NOT NULL CHECK (fill_notional>=0),
  allocated_buy_fee NUMERIC(18,2) NOT NULL DEFAULT 0,
  allocated_sell_fee NUMERIC(18,2) NOT NULL DEFAULT 0,
  allocated_sell_tax NUMERIC(18,2) NOT NULL DEFAULT 0,
  allocated_other_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
  rounding_residual_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
  stable_allocation_key VARCHAR(256) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(broker_cost_snapshot_id,minute_live_trade_id,allocation_side)
);

CREATE INDEX IF NOT EXISTS ix_minute_ma_live_signal_time
  ON minute_ma_live_signal_event(source_bar_time,minute_path_id);
CREATE INDEX IF NOT EXISTS ix_minute_ma_checkpoint_trade
  ON minute_ma_live_checkpoint_allocation(minute_live_trade_id,broker_event_time);

COMMIT;
