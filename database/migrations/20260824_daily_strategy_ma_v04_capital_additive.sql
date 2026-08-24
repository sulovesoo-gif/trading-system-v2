-- Daily MA V0.4: strategy-local realized-compound capital.
-- Additive only.  It never rewrites V0.3 PAPER/LIVE/operation history.
BEGIN;

ALTER TABLE daily_strategy_live_order_intent
    ADD COLUMN IF NOT EXISTS capital_epoch_no INTEGER NULL,
    ADD COLUMN IF NOT EXISTS strategy_compound_capital_at_signal NUMERIC(18,2) NULL,
    ADD COLUMN IF NOT EXISTS available_cash_snapshot NUMERIC(18,2) NULL,
    ADD COLUMN IF NOT EXISTS cash_gate_checked_at TIMESTAMP NULL;

ALTER TABLE daily_strategy_live_trade
    ADD COLUMN IF NOT EXISTS strategy_compound_capital_at_signal NUMERIC(18,2) NULL,
    ADD COLUMN IF NOT EXISTS available_cash_at_signal NUMERIC(18,2) NULL,
    ADD COLUMN IF NOT EXISTS capital_settled_at TIMESTAMP NULL;

CREATE TABLE IF NOT EXISTS daily_strategy_compound_capital (
    strategy_id VARCHAR NOT NULL REFERENCES daily_strategy_master(strategy_id),
    capital_epoch_no INTEGER NOT NULL CHECK (capital_epoch_no >= 1),
    source_operation_id BIGINT NOT NULL REFERENCES daily_strategy_operation(operation_id),
    epoch_initial_capital NUMERIC(18,2) NOT NULL CHECK (epoch_initial_capital > 0),
    strategy_compound_capital NUMERIC(18,2) NOT NULL,
    cumulative_net_realized_pnl NUMERIC(18,2) NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (strategy_id, capital_epoch_no),
    UNIQUE (source_operation_id),
    CHECK (strategy_compound_capital = epoch_initial_capital + cumulative_net_realized_pnl)
);

CREATE TABLE IF NOT EXISTS daily_strategy_live_capital_settlement (
    settlement_id UUID PRIMARY KEY,
    live_trade_id BIGINT NOT NULL UNIQUE REFERENCES daily_strategy_live_trade(live_trade_id),
    strategy_id VARCHAR NOT NULL,
    capital_epoch_no INTEGER NOT NULL,
    entry_filled_amount NUMERIC(18,2) NOT NULL CHECK (entry_filled_amount >= 0),
    exit_filled_amount NUMERIC(18,2) NOT NULL CHECK (exit_filled_amount >= 0),
    gross_realized_pnl NUMERIC(18,2) NOT NULL,
    buy_fee NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (buy_fee >= 0),
    sell_fee NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (sell_fee >= 0),
    sell_tax NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (sell_tax >= 0),
    other_cost_amount NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (other_cost_amount >= 0),
    net_realized_pnl NUMERIC(18,2) NOT NULL,
    settled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (strategy_id, capital_epoch_no)
        REFERENCES daily_strategy_compound_capital(strategy_id, capital_epoch_no),
    CHECK (net_realized_pnl = gross_realized_pnl - buy_fee - sell_fee - sell_tax - other_cost_amount)
);

CREATE TABLE IF NOT EXISTS daily_strategy_live_entry_skip (
    skip_id UUID PRIMARY KEY,
    strategy_id VARCHAR NOT NULL REFERENCES daily_strategy_master(strategy_id),
    paper_trade_id BIGINT NOT NULL REFERENCES daily_strategy_paper_trade(paper_trade_id),
    signal_event_key VARCHAR NOT NULL,
    intent_key CHAR(64) NOT NULL,
    capital_epoch_no INTEGER NOT NULL CHECK (capital_epoch_no >= 1),
    strategy_compound_capital_at_signal NUMERIC(18,2) NOT NULL,
    planned_quantity INTEGER NOT NULL CHECK (planned_quantity >= 0),
    planned_notional NUMERIC(18,2) NOT NULL CHECK (planned_notional >= 0),
    available_cash_snapshot NUMERIC(18,2) NOT NULL CHECK (available_cash_snapshot >= 0),
    skip_reason VARCHAR(64) NOT NULL CHECK (skip_reason IN ('INSUFFICIENT_AVAILABLE_CASH','ZERO_QUANTITY')),
    retry_allowed BOOLEAN NOT NULL DEFAULT FALSE CHECK (retry_allowed = FALSE),
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    skipped_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (strategy_id, signal_event_key),
    UNIQUE (intent_key)
);

CREATE INDEX IF NOT EXISTS ix_daily_strategy_capital_current
    ON daily_strategy_compound_capital(strategy_id, capital_epoch_no, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_daily_strategy_capital_settlement_epoch
    ON daily_strategy_live_capital_settlement(strategy_id, capital_epoch_no, settled_at DESC);
CREATE INDEX IF NOT EXISTS ix_daily_strategy_live_skip_strategy_time
    ON daily_strategy_live_entry_skip(strategy_id, skipped_at DESC);

COMMIT;
