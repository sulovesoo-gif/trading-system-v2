-- Daily MA V0.3 PAPER Runtime: additive durable event/transition state.
-- No LIVE/broker object, order endpoint, or existing historical row is changed.
BEGIN;

CREATE TABLE IF NOT EXISTS daily_strategy_trade_no_counter (
    strategy_id VARCHAR NOT NULL PRIMARY KEY
        REFERENCES daily_strategy_master(strategy_id),
    next_trade_no INTEGER NOT NULL CHECK (next_trade_no > 0),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO daily_strategy_trade_no_counter(strategy_id, next_trade_no)
SELECT m.strategy_id, COALESCE(MAX(p.trade_no), 0) + 1
  FROM daily_strategy_master m
  LEFT JOIN daily_strategy_paper_trade p ON p.strategy_id = m.strategy_id
 GROUP BY m.strategy_id
ON CONFLICT (strategy_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS daily_strategy_paper_event (
    paper_event_id BIGSERIAL PRIMARY KEY,
    strategy_id VARCHAR NOT NULL REFERENCES daily_strategy_master(strategy_id),
    signal_event_key VARCHAR NOT NULL,
    event_kind VARCHAR(32) NOT NULL CHECK (event_kind IN ('ENTRY', 'NORMAL_EXIT', 'DAY20_CHECK')),
    source_bar_time TIMESTAMP NOT NULL,
    snapshot_hash CHAR(64) NOT NULL,
    source_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome VARCHAR(40) NOT NULL CHECK (outcome IN ('CREATED', 'NO_EXECUTION_BAR', 'NO_SIGNAL', 'BLOCKED_INPUT_MISMATCH')),
    paper_trade_id BIGINT NULL REFERENCES daily_strategy_paper_trade(paper_trade_id),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (strategy_id, signal_event_key, event_kind)
);

CREATE INDEX IF NOT EXISTS ix_daily_strategy_paper_event_trade
    ON daily_strategy_paper_event(paper_trade_id, event_kind, source_bar_time);

CREATE TABLE IF NOT EXISTS daily_strategy_paper_transition (
    paper_transition_id BIGSERIAL PRIMARY KEY,
    paper_trade_id BIGINT NOT NULL REFERENCES daily_strategy_paper_trade(paper_trade_id),
    transition_key CHAR(64) NOT NULL UNIQUE,
    transition_type VARCHAR(32) NOT NULL CHECK (transition_type IN (
        'ENTRY_CREATED', 'DAY20_TRIGGERED', 'ACTUAL_EXIT', 'NORMAL_EXIT', 'NO_EXECUTION_BAR'
    )),
    source_bar_time TIMESTAMP NULL,
    execution_target_time TIMESTAMP NULL,
    snapshot_hash CHAR(64) NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (paper_trade_id, transition_type, source_bar_time)
);

CREATE INDEX IF NOT EXISTS ix_daily_strategy_paper_transition_recovery
    ON daily_strategy_paper_transition(paper_trade_id, transition_type, created_at);

CREATE TABLE IF NOT EXISTS daily_strategy_paper_runtime_cursor (
    runtime_name VARCHAR(64) PRIMARY KEY,
    last_completed_source_bar_time TIMESTAMP NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMIT;
