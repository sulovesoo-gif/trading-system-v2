-- Additive KIS cumulative-fill checkpoint.  It intentionally does not insert
-- synthetic rows into live_broker_fill, whose contract requires broker trade IDs.
BEGIN;
CREATE TABLE IF NOT EXISTS daily_strategy_live_fill_checkpoint (
  broker_order_id UUID PRIMARY KEY REFERENCES live_broker_order(broker_order_id),
  broker_order_number VARCHAR(64) NOT NULL,
  cumulative_filled_qty INTEGER NOT NULL CHECK (cumulative_filled_qty >= 0),
  cumulative_filled_amount NUMERIC(20,2) NOT NULL CHECK (cumulative_filled_amount >= 0),
  last_avg_fill_price NUMERIC(20,6) NOT NULL CHECK (last_avg_fill_price >= 0),
  last_broker_event_time TIMESTAMP NULL,
  version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
  checkpoint_status VARCHAR(48) NOT NULL DEFAULT 'ACTIVE' CHECK (checkpoint_status IN ('ACTIVE','BROKER_FILL_CHECKPOINT_REGRESSION')),
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS daily_strategy_live_checkpoint_allocation (
  allocation_id UUID PRIMARY KEY,
  broker_order_id UUID NOT NULL REFERENCES live_broker_order(broker_order_id),
  checkpoint_version INTEGER NOT NULL,
  ownership_id VARCHAR(160) NOT NULL,
  stock_code VARCHAR(32) NOT NULL,
  side VARCHAR(8) NOT NULL CHECK (side IN ('BUY','SELL')),
  delta_quantity INTEGER NOT NULL CHECK (delta_quantity > 0),
  delta_amount NUMERIC(20,2) NOT NULL CHECK (delta_amount > 0),
  broker_event_time TIMESTAMP NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (broker_order_id, checkpoint_version)
);
COMMIT;
