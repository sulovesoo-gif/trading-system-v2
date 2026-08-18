-- Additive ownership model.  No existing LIVE/RAW/research history is rewritten.
CREATE TABLE IF NOT EXISTS execution_logical_position (
    ownership_type VARCHAR(32) NOT NULL,
    ownership_id VARCHAR(120) NOT NULL,
    stock_code VARCHAR(32) NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    average_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
    realized_pnl NUMERIC(18,2) NOT NULL DEFAULT 0,
    last_fill_at TIMESTAMP,
    version INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (ownership_type, ownership_id, stock_code)
);
CREATE TABLE IF NOT EXISTS execution_fill_allocation (
    allocation_id UUID PRIMARY KEY,
    broker_order_id UUID NOT NULL,
    broker_trade_id VARCHAR(128) NOT NULL,
    ownership_type VARCHAR(32) NOT NULL,
    ownership_id VARCHAR(120) NOT NULL,
    stock_code VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL CHECK (side IN ('BUY','SELL')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    fill_price NUMERIC(18,2) NOT NULL,
    fee NUMERIC(18,2) NOT NULL DEFAULT 0,
    tax NUMERIC(18,2) NOT NULL DEFAULT 0,
    other_cost NUMERIC(18,2) NOT NULL DEFAULT 0,
    filled_at TIMESTAMP NOT NULL,
    idempotency_key CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS execution_reconciliation_audit (
    reconciliation_id BIGSERIAL PRIMARY KEY,
    stock_code VARCHAR(32) NOT NULL,
    broker_net_quantity INTEGER NOT NULL,
    attributed_quantity INTEGER NOT NULL,
    unattributed_quantity INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,
    checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_execution_position_stock ON execution_logical_position(stock_code);
CREATE INDEX IF NOT EXISTS ix_execution_fill_owner ON execution_fill_allocation(ownership_type, ownership_id, stock_code, filled_at);
