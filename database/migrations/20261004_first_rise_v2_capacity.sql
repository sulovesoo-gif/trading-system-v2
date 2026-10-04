-- V2.0 observation only. Existing orders, epochs and K_MODE PAPER are untouched.
BEGIN;
CREATE TABLE first_rise_capacity_day (
 business_date date PRIMARY KEY, config_row jsonb NOT NULL, config_error text,
 loaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE first_rise_j_shadow_trade (
 market_signal_id uuid PRIMARY KEY REFERENCES first_rise_j_market_signal(market_signal_id),
 contract text NOT NULL CHECK(contract='FIRST_RISE_SHADOW_FIXED10M_V2.0'),
 fixed_amount numeric NOT NULL CHECK(fixed_amount=10000000),
 quantity bigint NOT NULL CHECK(quantity>=0), cash_used numeric NOT NULL CHECK(cash_used>=0),
 entry_time timestamp NOT NULL, exit_time timestamp, exit_reason text,
 net_pnl numeric, net_return numeric, config_evidence jsonb NOT NULL,
 CHECK ((exit_time IS NULL AND net_pnl IS NULL AND net_return IS NULL)
     OR (exit_time IS NOT NULL AND exit_reason IS NOT NULL AND net_pnl IS NOT NULL))
);
CREATE TABLE first_rise_j_capacity_observation (
 trade_id uuid PRIMARY KEY REFERENCES first_rise_j_capital_binding(trade_id),
 market_signal_id uuid NOT NULL UNIQUE REFERENCES first_rise_j_shadow_trade(market_signal_id),
 stock_code varchar(16) NOT NULL, signal_sequence smallint NOT NULL CHECK(signal_sequence IN(1,2)),
 entry_time timestamp NOT NULL, exit_time timestamp, exit_reason text,
 comparison_status text NOT NULL CHECK(comparison_status IN('OPEN','PROVISIONAL','FINAL')),
 shadow_net_return numeric, live_return_provisional numeric, live_return_final numeric,
 evidence jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE first_rise_capacity_warning (
 window_size integer PRIMARY KEY CHECK(window_size>0),
 severity integer NOT NULL CHECK(severity BETWEEN 0 AND 3), revision bigint NOT NULL DEFAULT 0,
 evidence jsonb NOT NULL, updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE first_rise_capacity_alert (
 event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 window_size integer NOT NULL REFERENCES first_rise_capacity_warning(window_size),
 revision bigint NOT NULL, evidence jsonb NOT NULL,
 delivery_attempted_at timestamptz, delivery_status text, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(window_size,revision)
);
CREATE TABLE first_rise_v2_order_observation (
 broker_order_id uuid PRIMARY KEY REFERENCES live_broker_order(broker_order_id),
 first_fill_observed_at timestamp, terminal_observed_at timestamp,
 had_partial_fill boolean NOT NULL DEFAULT FALSE,
 cumulative_quantity bigint NOT NULL DEFAULT 0 CHECK(cumulative_quantity>=0),
 cumulative_amount numeric NOT NULL DEFAULT 0 CHECK(cumulative_amount>=0),
 timing_basis text NOT NULL DEFAULT 'KIS_CUMULATIVE_POLL_OBSERVATION'
);
COMMIT;
