-- V0.4.2: KIS-authoritative product-day broker-cost snapshots.
-- Additive.  No synthetic broker fill or per-order fee is created.
BEGIN;

CREATE TABLE IF NOT EXISTS daily_strategy_live_broker_cost_snapshot (
    broker_cost_snapshot_id UUID PRIMARY KEY,
    trade_date DATE NOT NULL,
    execution_stock_code VARCHAR(32) NOT NULL,
    broker_buy_fee NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (broker_buy_fee >= 0),
    broker_sell_fee NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (broker_sell_fee >= 0),
    broker_sell_tax NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (broker_sell_tax >= 0),
    broker_other_cost NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (broker_other_cost >= 0),
    buy_fill_notional_denominator NUMERIC(20,2) NOT NULL DEFAULT 0 CHECK (buy_fill_notional_denominator >= 0),
    sell_fill_notional_denominator NUMERIC(20,2) NOT NULL DEFAULT 0 CHECK (sell_fill_notional_denominator >= 0),
    broker_snapshot_at TIMESTAMP NOT NULL,
    finalization_status VARCHAR(48) NOT NULL CHECK (finalization_status IN (
      'PENDING_BROKER_COST','FINALIZED','BROKER_COST_ATTRIBUTION_BLOCKED','BROKER_COST_SNAPSHOT_REGRESSION'
    )),
    reconciliation_status VARCHAR(48) NOT NULL DEFAULT 'PENDING',
    finalized_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (trade_date, execution_stock_code)
);

CREATE TABLE IF NOT EXISTS daily_strategy_live_broker_cost_allocation (
    broker_cost_snapshot_id UUID NOT NULL REFERENCES daily_strategy_live_broker_cost_snapshot(broker_cost_snapshot_id),
    live_trade_id BIGINT NOT NULL REFERENCES daily_strategy_live_trade(live_trade_id),
    allocation_side VARCHAR(8) NOT NULL CHECK (allocation_side IN ('BUY','SELL')),
    fill_notional NUMERIC(20,2) NOT NULL CHECK (fill_notional >= 0),
    allocated_buy_fee NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (allocated_buy_fee >= 0),
    allocated_sell_fee NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (allocated_sell_fee >= 0),
    allocated_sell_tax NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (allocated_sell_tax >= 0),
    allocated_other_cost NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (allocated_other_cost >= 0),
    rounding_residual_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
    stable_allocation_key VARCHAR(256) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (broker_cost_snapshot_id, live_trade_id, allocation_side),
    UNIQUE (broker_cost_snapshot_id, allocation_side, stable_allocation_key)
);

CREATE INDEX IF NOT EXISTS ix_daily_ma_cost_allocation_live_trade
    ON daily_strategy_live_broker_cost_allocation(live_trade_id);

COMMIT;
