BEGIN;

CREATE TABLE IF NOT EXISTS first_rise_breakout_candidate_event (
    candidate_event_id UUID PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    business_date DATE NOT NULL,
    condition_name TEXT NOT NULL,
    condition_seq TEXT NOT NULL,
    stock_code VARCHAR(12) NOT NULL,
    stock_name TEXT,
    discovered_at TIMESTAMP NOT NULL,
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (business_date, condition_name, stock_code)
);

CREATE TABLE IF NOT EXISTS first_rise_breakout_candidate_state (
    candidate_event_id UUID PRIMARY KEY
        REFERENCES first_rise_breakout_candidate_event(candidate_event_id),
    state_code TEXT NOT NULL CHECK (state_code IN (
        'DISCOVERED','TRACKING','PULLBACK','WAIT_REBREAK',
        'PAPER_ENTERED','PAPER_EXITED','REJECTED','EXPIRED'
    )),
    peak_price NUMERIC(20,6),
    peak_time TIMESTAMP,
    pullback_low_price NUMERIC(20,6),
    pullback_pct NUMERIC(12,8),
    last_observed_at TIMESTAMP,
    last_observed_price NUMERIC(20,6),
    entry_event_key TEXT,
    state_version BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS first_rise_breakout_state_transition (
    transition_id UUID PRIMARY KEY,
    transition_key TEXT NOT NULL UNIQUE,
    candidate_event_id UUID NOT NULL
        REFERENCES first_rise_breakout_candidate_event(candidate_event_id),
    from_state TEXT,
    to_state TEXT NOT NULL CHECK (to_state IN (
        'DISCOVERED','TRACKING','PULLBACK','WAIT_REBREAK',
        'PAPER_ENTERED','PAPER_EXITED','REJECTED','EXPIRED'
    )),
    observed_at TIMESTAMP NOT NULL,
    observed_price NUMERIC(20,6),
    peak_price NUMERIC(20,6),
    peak_time TIMESTAMP,
    pullback_pct NUMERIC(12,8),
    reason_code TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_first_rise_breakout_transition_candidate_time
    ON first_rise_breakout_state_transition(candidate_event_id, observed_at);

CREATE TABLE IF NOT EXISTS first_rise_breakout_paper_trade (
    paper_trade_id BIGSERIAL PRIMARY KEY,
    candidate_event_id UUID NOT NULL UNIQUE
        REFERENCES first_rise_breakout_candidate_event(candidate_event_id),
    entry_event_key TEXT NOT NULL UNIQUE,
    stock_code VARCHAR(12) NOT NULL,
    trade_status TEXT NOT NULL CHECK (trade_status IN ('OPEN','CLOSED')),
    entry_signal_time TIMESTAMP NOT NULL,
    entry_execution_time TIMESTAMP NOT NULL,
    entry_execution_price NUMERIC(20,6) NOT NULL CHECK (entry_execution_price > 0),
    exit_event_key TEXT UNIQUE,
    exit_signal_time TIMESTAMP,
    exit_execution_time TIMESTAMP,
    exit_execution_price NUMERIC(20,6),
    exit_reason TEXT,
    entry_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    exit_evidence JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (trade_status = 'OPEN' AND exit_execution_time IS NULL AND exit_execution_price IS NULL)
        OR
        (trade_status = 'CLOSED' AND exit_execution_time IS NOT NULL AND exit_execution_price > 0)
    )
);

CREATE INDEX IF NOT EXISTS ix_first_rise_breakout_candidate_active
    ON first_rise_breakout_candidate_event(business_date, stock_code);
CREATE INDEX IF NOT EXISTS ix_first_rise_breakout_paper_status
    ON first_rise_breakout_paper_trade(trade_status, stock_code);

COMMIT;
