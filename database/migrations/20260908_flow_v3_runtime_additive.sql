BEGIN;

CREATE TABLE IF NOT EXISTS flow_v3_minute_state (
    business_date DATE NOT NULL,
    stock_code VARCHAR(20) NOT NULL CHECK (stock_code IN ('000660','005930')),
    bar_time TIMESTAMP(0) NOT NULL,
    underlying_close NUMERIC(24,6) NOT NULL,
    aggressive_buy_amount NUMERIC(30,6) NOT NULL,
    aggressive_sell_amount NUMERIC(30,6) NOT NULL,
    flow_value NUMERIC(30,6) NOT NULL,
    velocity_value NUMERIC(30,6),
    program_net_flow NUMERIC(30,6),
    program_velocity NUMERIC(30,6),
    long_absorption BOOLEAN NOT NULL,
    short_absorption BOOLEAN NOT NULL,
    snapshot_count INTEGER NOT NULL DEFAULT 0 CHECK (snapshot_count >= 0),
    execution_source_gap BOOLEAN NOT NULL DEFAULT FALSE,
    execution_duplicate_rows INTEGER NOT NULL DEFAULT 0 CHECK (execution_duplicate_rows >= 0),
    program_source_gap BOOLEAN NOT NULL DEFAULT FALSE,
    program_duplicate_rows INTEGER NOT NULL DEFAULT 0 CHECK (program_duplicate_rows >= 0),
    orderbook_source_gap BOOLEAN NOT NULL DEFAULT FALSE,
    orderbook_duplicate_rows INTEGER NOT NULL DEFAULT 0 CHECK (orderbook_duplicate_rows >= 0),
    quality_code VARCHAR(40) NOT NULL,
    is_complete BOOLEAN NOT NULL,
    flow_averages JSONB NOT NULL,
    velocity_averages JSONB NOT NULL,
    flow_crosses JSONB NOT NULL,
    velocity_crosses JSONB NOT NULL,
    calculated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (stock_code,bar_time),
    CHECK (business_date=bar_time::date),
    CHECK (is_complete=(quality_code='OK'))
);

CREATE INDEX IF NOT EXISTS ix_flow_v3_minute_state_date
    ON flow_v3_minute_state (business_date,stock_code,bar_time);

CREATE TABLE IF NOT EXISTS flow_v3_runtime_cursor (
    consumer_code VARCHAR(60) NOT NULL,
    stock_code VARCHAR(20) NOT NULL CHECK (stock_code IN ('000660','005930')),
    last_bar_time TIMESTAMP(0) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (consumer_code,stock_code)
);

CREATE TABLE IF NOT EXISTS flow_v3_runtime_entry_event (
    event_id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR(20) NOT NULL REFERENCES flow_v3_strategy_master(strategy_id),
    entry_event_key VARCHAR(300) NOT NULL,
    event_status VARCHAR(16) NOT NULL DEFAULT 'PENDING'
        CHECK (event_status IN ('PENDING','EXECUTED','EXPIRED')),
    business_date DATE NOT NULL,
    stock_code VARCHAR(20) NOT NULL CHECK (stock_code IN ('000660','005930')),
    direction VARCHAR(10) NOT NULL CHECK (direction IN ('LONG','SHORT')),
    entry_family_code VARCHAR(10) NOT NULL CHECK (entry_family_code IN ('F1','F2','F3','F4')),
    entry_fast_period SMALLINT NOT NULL,
    entry_slow_period SMALLINT NOT NULL,
    program_condition_code VARCHAR(10) NOT NULL CHECK (program_condition_code IN ('P0','P1','P2')),
    exit_fast_period SMALLINT NOT NULL,
    exit_slow_period SMALLINT NOT NULL,
    exit_policy_code VARCHAR(20) NOT NULL CHECK (exit_policy_code IN ('SIGNAL_EOD','SIGNAL_HOLD')),
    execution_code VARCHAR(20) NOT NULL,
    entry_signal_time TIMESTAMP(0) NOT NULL,
    entry_flow_value NUMERIC(30,6),
    entry_velocity_value NUMERIC(30,6),
    entry_program_value NUMERIC(30,6),
    entry_quality_code VARCHAR(40),
    event_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    entry_execution_time TIMESTAMP(0),
    entry_execution_price NUMERIC(24,6),
    paper_trade_id BIGINT REFERENCES flow_v3_paper_trade(paper_trade_id),
    failure_reason VARCHAR(60),
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (strategy_id,entry_event_key),
    CHECK (business_date=entry_signal_time::date),
    CHECK ((event_status<>'EXECUTED') OR paper_trade_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_flow_v3_runtime_entry_pending
    ON flow_v3_runtime_entry_event (entry_signal_time,strategy_id)
    WHERE event_status='PENDING';

COMMENT ON TABLE flow_v3_minute_state IS
    'FLOW V3 completed KRX minute state derived from immutable L0 RAW; daily FLOW/Velocity reset';
COMMENT ON TABLE flow_v3_runtime_entry_event IS
    'Durable entry signal lifecycle preserving late execution-product KRX minute proxies';
COMMENT ON TABLE flow_v3_runtime_cursor IS
    'Restart-safe sequential completed-minute consumer cursor';

COMMIT;
