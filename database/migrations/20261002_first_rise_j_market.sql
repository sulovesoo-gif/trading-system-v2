-- Additive J research lifecycle. Do not rewrite legacy candidate/PAPER history.
BEGIN;
CREATE TABLE first_rise_j_candidate (
    candidate_event_id uuid PRIMARY KEY REFERENCES first_rise_breakout_candidate_event(candidate_event_id),
    business_date date NOT NULL,
    stock_code varchar(16) NOT NULL,
    state_snapshot jsonb NOT NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX first_rise_j_candidate_day_idx ON first_rise_j_candidate(business_date,stock_code);
CREATE TABLE first_rise_j_market_signal (
    market_signal_id uuid PRIMARY KEY,
    candidate_event_id uuid NOT NULL REFERENCES first_rise_j_candidate(candidate_event_id),
    business_date date NOT NULL,
    stock_code varchar(16) NOT NULL,
    signal_sequence smallint NOT NULL CHECK (signal_sequence IN (1,2)),
    prior_market_signal_id uuid REFERENCES first_rise_j_market_signal(market_signal_id),
    entry_event_key text NOT NULL UNIQUE,
    entry_signal_time timestamp NOT NULL,
    raw_entry_price numeric NOT NULL CHECK (raw_entry_price > 0),
    entry_evidence jsonb NOT NULL,
    exit_signal_time timestamp,
    exit_execution_time timestamp,
    raw_exit_price numeric,
    exit_reason text CHECK (exit_reason IN ('STOP_ENTRY_BREAK','BOOK_TENKAN_PROFIT','SESSION_CLOSE')),
    exit_evidence jsonb,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (business_date,stock_code,signal_sequence),
    CHECK ((signal_sequence=1 AND prior_market_signal_id IS NULL)
        OR (signal_sequence=2 AND prior_market_signal_id IS NOT NULL)),
    CHECK ((exit_reason IS NULL AND exit_signal_time IS NULL AND exit_execution_time IS NULL AND raw_exit_price IS NULL)
        OR (exit_reason IS NOT NULL AND exit_signal_time IS NOT NULL AND exit_execution_time IS NOT NULL
            AND raw_exit_price > 0 AND exit_execution_time > entry_signal_time))
);
CREATE INDEX first_rise_j_market_open_idx ON first_rise_j_market_signal(business_date)
    WHERE exit_reason IS NULL;
CREATE TABLE first_rise_j_paper_trade (
    market_signal_id uuid PRIMARY KEY REFERENCES first_rise_j_market_signal(market_signal_id),
    status text NOT NULL CHECK (status IN ('OPEN','CLOSED','OVERLAP_SKIP','NO_CAPITAL','OUTSIDE_PAPER_WINDOW')),
    quantity integer NOT NULL CHECK (quantity >= 0),
    capital_before numeric,
    cash_remaining numeric,
    entry_execution_price numeric,
    exit_execution_price numeric,
    realized_pnl numeric,
    capital_after numeric,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((status IN ('OPEN','CLOSED') AND quantity > 0 AND capital_before IS NOT NULL)
        OR (status NOT IN ('OPEN','CLOSED') AND quantity=0))
);
COMMENT ON TABLE first_rise_j_paper_trade IS
 'J research K_MODE=1 fills only; skipped PAPER orders do not end market signals. Not LIVE capital.';
COMMIT;
