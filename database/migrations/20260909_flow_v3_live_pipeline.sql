-- FLOW-only No-SEND execution ledgers. Original PAPER/RAW are untouched.
BEGIN;
SET LOCAL lock_timeout='5s';
CREATE TABLE IF NOT EXISTS flow_v3_live_capital (
 strategy_id varchar(20) PRIMARY KEY REFERENCES flow_v3_live_preparation(strategy_id),
 operation_id bigint NOT NULL REFERENCES flow_v3_strategy_operation(operation_id),
 initial_price_date date NOT NULL, initial_close numeric NOT NULL CHECK(initial_close>0),
 initial_capital numeric NOT NULL CHECK(initial_capital=initial_close*1.5),
 realized_net numeric NOT NULL DEFAULT 0,
 current_capital numeric NOT NULL,
 version bigint NOT NULL DEFAULT 0,
 activated_at timestamp NOT NULL,
 reference_price numeric CHECK(reference_price>0), reference_observed_at timestamp,
 next_quantity bigint CHECK(next_quantity>=0),
 last_error text, updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(current_capital=initial_capital+realized_net)
);
CREATE TABLE IF NOT EXISTS flow_v3_live_intent (
 intent_id uuid PRIMARY KEY,
 strategy_id varchar(20) NOT NULL REFERENCES flow_v3_live_capital(strategy_id),
 event_id bigint NOT NULL REFERENCES flow_v3_runtime_entry_event(event_id),
 entry_event_key varchar(300) NOT NULL,
 paper_trade_id bigint REFERENCES flow_v3_paper_trade(paper_trade_id),
 live_trade_id bigint REFERENCES flow_v3_live_trade(live_trade_id),
 side text NOT NULL CHECK(side IN ('BUY','SELL')),
 lifecycle_key text NOT NULL UNIQUE,
 signal_time timestamp NOT NULL, execution_not_before timestamp NOT NULL,
 exit_reason text CHECK(exit_reason IN ('NORMAL_EXIT','SIGNAL_EOD')),
 execution_code text NOT NULL CHECK(execution_code IN ('0193T0','0197X0')),
 quantity bigint CHECK(quantity>0), reference_price numeric CHECK(reference_price>0),
 reference_observed_at timestamp, capital_at_entry numeric,
 status text NOT NULL CHECK(status IN ('WAITING_REFERENCE','BLOCKED','READY_NO_SEND','ACK','REJECTED','UNKNOWN','PARTIAL','FILLED','CANCELLED')),
 reason text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK((side='BUY' AND exit_reason IS NULL) OR (side='SELL' AND live_trade_id IS NOT NULL AND exit_reason IS NOT NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_flow_v3_live_entry_identity
 ON flow_v3_live_intent(strategy_id,entry_event_key) WHERE side='BUY';
-- One exit lifecycle per lot, never one lifecycle per strategy. Rejections and
-- UNKNOWN cannot automatically create a replacement order.
CREATE UNIQUE INDEX IF NOT EXISTS ux_flow_v3_live_lot_exit
 ON flow_v3_live_intent(live_trade_id) WHERE side='SELL';
CREATE TABLE IF NOT EXISTS flow_v3_live_order (
 broker_order_id uuid PRIMARY KEY,
 intent_id uuid NOT NULL UNIQUE REFERENCES flow_v3_live_intent(intent_id),
 request_payload jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('READY_NO_SEND','SUBMITTING','ACK','REJECTED','UNKNOWN','PARTIAL','FILLED','CANCELLED')),
 send_enabled boolean NOT NULL DEFAULT false CHECK(NOT send_enabled),
 post_attempt_count integer NOT NULL DEFAULT 0 CHECK(post_attempt_count=0),
 broker_order_number text, broker_order_date date,
 response_code text, response_message text, responded_at timestamp,
 cumulative_quantity bigint NOT NULL DEFAULT 0 CHECK(cumulative_quantity>=0),
 cumulative_amount numeric NOT NULL DEFAULT 0 CHECK(cumulative_amount>=0),
 checkpoint_version bigint NOT NULL DEFAULT 0,
 last_observed_at timestamp, last_error text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(broker_order_date,broker_order_number)
);
CREATE TABLE IF NOT EXISTS flow_v3_live_fill_checkpoint (
 broker_order_id uuid NOT NULL REFERENCES flow_v3_live_order(broker_order_id),
 checkpoint_version bigint NOT NULL,
 live_trade_id bigint NOT NULL REFERENCES flow_v3_live_trade(live_trade_id),
 broker_trade_date date NOT NULL,
 observed_at timestamp NOT NULL,
 -- History supplies cumulative fills, not individual exchange fill IDs/times.
 actual_fill_time timestamp,
 side text NOT NULL CHECK(side IN ('BUY','SELL')),
 delta_quantity bigint NOT NULL CHECK(delta_quantity>0),
 delta_amount numeric NOT NULL CHECK(delta_amount>0),
 cumulative_quantity bigint NOT NULL,
 cumulative_amount numeric NOT NULL,
 PRIMARY KEY(broker_order_id,checkpoint_version)
);
CREATE TABLE IF NOT EXISTS flow_v3_live_entry_release (
 entry_intent_id uuid PRIMARY KEY REFERENCES flow_v3_live_intent(intent_id),
 broker_order_id uuid NOT NULL UNIQUE REFERENCES flow_v3_live_order(broker_order_id),
 exit_signal_time timestamp NOT NULL, exit_reason text NOT NULL,
 requested_at timestamp NOT NULL,
 status text NOT NULL CHECK(status IN ('WAIT_HISTORY','CANCEL_READY_NO_SEND','CANCEL_ACK','UNKNOWN','CONFIRMED')),
 cancellable_quantity bigint, cancel_payload jsonb,
 cancel_response_code text, cancel_response_message text, cancel_order_number text,
 cancel_responded_at timestamp, history_observed_at timestamp,
 final_quantity bigint CHECK(final_quantity>=0),
 post_attempt_count integer NOT NULL DEFAULT 0 CHECK(post_attempt_count=0),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS flow_v3_live_lot (
 live_trade_id bigint PRIMARY KEY REFERENCES flow_v3_live_trade(live_trade_id),
 entry_intent_id uuid NOT NULL UNIQUE REFERENCES flow_v3_live_intent(intent_id),
 strategy_id varchar(20) NOT NULL REFERENCES flow_v3_live_capital(strategy_id),
 entry_event_key varchar(300) NOT NULL,
 bought_quantity bigint NOT NULL DEFAULT 0,
 sold_quantity bigint NOT NULL DEFAULT 0,
 buy_amount numeric NOT NULL DEFAULT 0, sell_amount numeric NOT NULL DEFAULT 0,
 last_source_bar timestamp,
 exposure_status text NOT NULL DEFAULT 'OPEN' CHECK(exposure_status IN ('OPEN','EXIT_PENDING','FILLED_COST_PENDING','SETTLED','EOD_EXIT_UNFILLED')),
 CHECK(bought_quantity>=sold_quantity AND sold_quantity>=0),
 CHECK(buy_amount>=0 AND sell_amount>=0),
 UNIQUE(strategy_id,entry_event_key)
);
CREATE TABLE IF NOT EXISTS flow_v3_live_settlement (
 live_trade_id bigint PRIMARY KEY REFERENCES flow_v3_live_lot(live_trade_id),
 strategy_id varchar(20) NOT NULL REFERENCES flow_v3_live_capital(strategy_id),
 gross_pnl numeric NOT NULL, buy_fee numeric NOT NULL CHECK(buy_fee>=0),
 sell_fee numeric NOT NULL CHECK(sell_fee>=0), sell_tax numeric NOT NULL CHECK(sell_tax>=0),
 other_cost numeric NOT NULL CHECK(other_cost>=0), net_pnl numeric NOT NULL,
 capital_before numeric NOT NULL, capital_after numeric NOT NULL,
 settled_at timestamptz NOT NULL DEFAULT now(),
 CHECK(net_pnl=gross_pnl-buy_fee-sell_fee-sell_tax-other_cost),
 CHECK(capital_after=capital_before+net_pnl)
);
-- Distinguish actual broker day from observer wall clock; never fabricate a
-- per-fill market timestamp from daily-history order_time.
ALTER TABLE flow_v3_live_checkpoint_allocation ADD COLUMN IF NOT EXISTS broker_trade_date date;
ALTER TABLE flow_v3_live_checkpoint_allocation ALTER COLUMN broker_event_time DROP NOT NULL;
CREATE TABLE IF NOT EXISTS flow_v3_live_worker_status (
 worker_code text PRIMARY KEY, heartbeat_at timestamptz NOT NULL,
 cycle_result jsonb NOT NULL, last_error text,
 send_enabled boolean NOT NULL DEFAULT false CHECK(NOT send_enabled)
);
COMMIT;
