BEGIN;

CREATE TABLE IF NOT EXISTS first_rise_breakout_condition_hit (
    condition_hit_id UUID PRIMARY KEY,
    poll_time TIMESTAMP NOT NULL,
    business_date DATE NOT NULL,
    condition_name TEXT NOT NULL,
    condition_seq TEXT NOT NULL,
    stock_code VARCHAR(12) NOT NULL,
    stock_name TEXT,
    result_rank INTEGER NOT NULL CHECK (result_rank > 0),
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (poll_time, condition_name, condition_seq, stock_code)
);

CREATE INDEX IF NOT EXISTS ix_first_rise_breakout_condition_hit_date_poll
    ON first_rise_breakout_condition_hit(business_date, condition_name, poll_time);

COMMIT;
