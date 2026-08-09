-- Non-destructive research-only persistence.  RAW and live analysis tables are never changed.
CREATE TABLE IF NOT EXISTS research_run (
    run_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    observation_code VARCHAR(20) NOT NULL DEFAULT 'COMPLETE',
    initial_capital NUMERIC(20,2) NOT NULL DEFAULT 10000000,
    fee_rate NUMERIC(12,8) NOT NULL DEFAULT 0,
    slippage_rate NUMERIC(12,8) NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS research_feature (
    run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    trading_date DATE NOT NULL, stock_code VARCHAR(20) NOT NULL,
    observation_code VARCHAR(20) NOT NULL, observation_time TIMESTAMP NOT NULL,
    price NUMERIC(18,2) NOT NULL, ma3 NUMERIC(18,8), ma5 NUMERIC(18,8), ma10 NUMERIC(18,8),
    ma10_direction VARCHAR(10), data_status VARCHAR(30) NOT NULL,
    PRIMARY KEY (run_id, stock_code, observation_code, observation_time)
);

CREATE TABLE IF NOT EXISTS research_signal_event (
    event_id BIGSERIAL PRIMARY KEY, run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    trading_date DATE NOT NULL, signal_source_stock_code VARCHAR(20) NOT NULL,
    observation_code VARCHAR(20) NOT NULL, signal_time TIMESTAMP NOT NULL,
    strategy_code VARCHAR(30) NOT NULL, signal_type VARCHAR(30) NOT NULL, direction VARCHAR(10) NOT NULL,
    signal_price NUMERIC(18,2) NOT NULL, ma3 NUMERIC(18,8), ma5 NUMERIC(18,8), ma10 NUMERIC(18,8),
    ma10_direction VARCHAR(10), pending_yn CHAR(1) NOT NULL DEFAULT 'N', pending_started_at TIMESTAMP,
    confirm_time TIMESTAMP, session_code VARCHAR(30), data_status VARCHAR(30) NOT NULL,
    UNIQUE(run_id, signal_source_stock_code, observation_code, signal_time, strategy_code, signal_type, direction)
);

CREATE TABLE IF NOT EXISTS research_trade_cycle (
    cycle_id BIGSERIAL PRIMARY KEY, run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    trading_date DATE NOT NULL, trade_stock_code VARCHAR(20) NOT NULL, signal_source_stock_code VARCHAR(20) NOT NULL,
    exit_signal_source_stock_code VARCHAR(20), strategy_code VARCHAR(30) NOT NULL, observation_code VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL, entry_signal_time TIMESTAMP, entry_confirm_time TIMESTAMP, entry_time TIMESTAMP NOT NULL,
    entry_price NUMERIC(18,2) NOT NULL, exit_signal_time TIMESTAMP, exit_time TIMESTAMP, exit_price NUMERIC(18,2),
    exit_type VARCHAR(30), quantity BIGINT NOT NULL, invested_amount NUMERIC(20,2) NOT NULL,
    realized_profit NUMERIC(20,2), invested_return_rate NUMERIC(18,8), capital_return_rate NUMERIC(18,8),
    holding_seconds BIGINT, data_status VARCHAR(30) NOT NULL DEFAULT 'NORMAL',
    UNIQUE(run_id, trade_stock_code, signal_source_stock_code, strategy_code, observation_code, entry_time)
);

CREATE TABLE IF NOT EXISTS research_trade_leg (
    cycle_id BIGINT NOT NULL REFERENCES research_trade_cycle(cycle_id) ON DELETE CASCADE,
    signal_type VARCHAR(30) NOT NULL, entry_time TIMESTAMP NOT NULL, entry_price NUMERIC(18,2) NOT NULL,
    entry_ratio NUMERIC(12,8) NOT NULL, quantity BIGINT NOT NULL, invested_amount NUMERIC(20,2) NOT NULL,
    PRIMARY KEY(cycle_id, signal_type)
);

CREATE TABLE IF NOT EXISTS research_performance_daily (
    run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    trading_date DATE NOT NULL, trade_stock_code VARCHAR(20) NOT NULL,
    signal_source_stock_code VARCHAR(20) NOT NULL, strategy_code VARCHAR(30) NOT NULL,
    observation_code VARCHAR(20) NOT NULL, direction VARCHAR(10) NOT NULL,
    session_code VARCHAR(30), daily_return_rate NUMERIC(18,8), daily_market_direction VARCHAR(10),
    closed_count INTEGER NOT NULL DEFAULT 0, win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0, flat_count INTEGER NOT NULL DEFAULT 0,
    realized_profit NUMERIC(20,2) NOT NULL DEFAULT 0, invested_amount NUMERIC(20,2) NOT NULL DEFAULT 0,
    invested_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0, capital_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0,
    avg_trade_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0, avg_holding_seconds NUMERIC(20,2) NOT NULL DEFAULT 0,
    signal_exit_profit NUMERIC(20,2) NOT NULL DEFAULT 0, session_close_profit NUMERIC(20,2) NOT NULL DEFAULT 0,
    PRIMARY KEY(run_id,trading_date,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction,session_code)
);

CREATE TABLE IF NOT EXISTS research_performance_period (
    run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    start_date DATE NOT NULL, end_date DATE NOT NULL, trade_stock_code VARCHAR(20) NOT NULL,
    signal_source_stock_code VARCHAR(20) NOT NULL, strategy_code VARCHAR(30) NOT NULL,
    observation_code VARCHAR(20) NOT NULL, direction VARCHAR(10) NOT NULL,
    closed_count INTEGER NOT NULL DEFAULT 0, win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0, flat_count INTEGER NOT NULL DEFAULT 0,
    realized_profit NUMERIC(20,2) NOT NULL DEFAULT 0, invested_amount NUMERIC(20,2) NOT NULL DEFAULT 0,
    invested_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0, capital_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0,
    avg_trade_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0, avg_holding_seconds NUMERIC(20,2) NOT NULL DEFAULT 0,
    signal_exit_profit NUMERIC(20,2) NOT NULL DEFAULT 0, session_close_profit NUMERIC(20,2) NOT NULL DEFAULT 0,
    PRIMARY KEY(run_id,start_date,end_date,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction)
);

CREATE INDEX IF NOT EXISTS ix_research_feature_run_stock_time ON research_feature(run_id, stock_code, observation_time);
CREATE INDEX IF NOT EXISTS ix_research_cycle_run_time ON research_trade_cycle(run_id, entry_time);
