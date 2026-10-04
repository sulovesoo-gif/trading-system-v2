-- Additive FIRST_RISE execution identities. No activation is created here.
BEGIN;
CREATE TABLE first_rise_j_activation (
    strategy_id text PRIMARY KEY CHECK(strategy_id='FIRST_RISE_J_V1.3'),
    effective_from timestamp NOT NULL,
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE first_rise_j_live_intent (
    intent_id uuid PRIMARY KEY,
    market_signal_id uuid NOT NULL REFERENCES first_rise_j_market_signal(market_signal_id),
    trade_id uuid REFERENCES first_rise_j_capital_binding(trade_id),
    side text NOT NULL CHECK(side IN ('BUY','SELL')),
    generation integer NOT NULL DEFAULT 0 CHECK(generation>=0 AND (side='SELL' OR generation=0)),
    signal_time timestamp NOT NULL,
    order_request_id uuid UNIQUE REFERENCES live_order_request(order_request_id),
    planning_reason text NOT NULL DEFAULT 'READY',
    sizing_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(market_signal_id,side,generation)
);
CREATE TABLE first_rise_j_cancel_request (
    broker_order_id uuid PRIMARY KEY REFERENCES live_broker_order(broker_order_id),
    cancel_key text NOT NULL UNIQUE,
    original_order_number text NOT NULL,
    branch text NOT NULL,
    cancellable_quantity integer NOT NULL CHECK(cancellable_quantity>0),
    status text NOT NULL CHECK(status IN ('READY','UNKNOWN','ACK','REJECTED','CONFIRMED')),
    post_attempted_at timestamp,
    response jsonb,
    terminal_confirmed_at timestamp,
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE first_rise_j_fill_checkpoint (
    broker_order_id uuid PRIMARY KEY REFERENCES live_broker_order(broker_order_id),
    cumulative_quantity integer NOT NULL DEFAULT 0 CHECK(cumulative_quantity>=0),
    cumulative_amount numeric NOT NULL DEFAULT 0 CHECK(cumulative_amount>=0),
    average_price numeric NOT NULL DEFAULT 0,
    event_time timestamp,
    version integer NOT NULL DEFAULT 0 CHECK(version>=0),
    status text NOT NULL DEFAULT 'ACTIVE'
);
COMMENT ON TABLE first_rise_j_activation IS 'Durable no-replay boundary, not an enable/SEND flag. Never reset on restart.';
COMMIT;
