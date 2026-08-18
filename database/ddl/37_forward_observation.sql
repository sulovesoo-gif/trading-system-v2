-- Additive FORWARD_OBSERVATION registry.  Candidate selection is always explicit.
CREATE TABLE IF NOT EXISTS forward_execution_path (
    forward_execution_id VARCHAR(64) PRIMARY KEY,
    entry_identity VARCHAR(256) NOT NULL,
    exit_identity VARCHAR(256) NOT NULL,
    execution_stock_code VARCHAR(32) NOT NULL,
    active_yn CHAR(1) NOT NULL DEFAULT 'N' CHECK (active_yn IN ('Y','N')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (entry_identity, exit_identity, execution_stock_code)
);
CREATE TABLE IF NOT EXISTS forward_candidate (
    forward_candidate_id VARCHAR(64) PRIMARY KEY,
    forward_execution_id VARCHAR(64) NOT NULL REFERENCES forward_execution_path(forward_execution_id),
    strategy_reference VARCHAR(256) NOT NULL,
    signal_stock_code VARCHAR(32) NOT NULL,
    selection_reason VARCHAR(512) NOT NULL,
    approved_at TIMESTAMP NOT NULL,
    approved_by VARCHAR(128) NOT NULL,
    active_yn CHAR(1) NOT NULL DEFAULT 'N' CHECK (active_yn IN ('Y','N')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS forward_performance_snapshot (
    forward_execution_id VARCHAR(64) PRIMARY KEY REFERENCES forward_execution_path(forward_execution_id),
    actual_1share_pnl NUMERIC(18,2) NOT NULL DEFAULT 0,
    cost_adjusted_actual_pnl NUMERIC(18,2) NOT NULL DEFAULT 0,
    cumulative_simple_return NUMERIC(18,8) NOT NULL DEFAULT 0,
    normalized_strategy_return NUMERIC(18,8) NOT NULL DEFAULT 0,
    compound_equity NUMERIC(18,2) NOT NULL DEFAULT 0,
    mdd NUMERIC(18,8) NOT NULL DEFAULT 0,
    trade_count INTEGER NOT NULL DEFAULT 0,
    win_rate NUMERIC(18,8) NOT NULL DEFAULT 0,
    profit_factor NUMERIC(18,8) NOT NULL DEFAULT 0,
    consecutive_losses INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
