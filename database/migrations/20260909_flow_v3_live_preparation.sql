-- Non-submittable LIVE preparation. No operation promotion or broker authorization.
BEGIN;
SET LOCAL lock_timeout='5s';
CREATE TABLE IF NOT EXISTS flow_v3_live_preparation (
 strategy_id varchar(20) PRIMARY KEY REFERENCES flow_v3_strategy_master(strategy_id),
 direction varchar(5) NOT NULL CHECK(direction IN ('LONG','SHORT')),
 stock_code varchar(20) NOT NULL CHECK(stock_code='000660'),
 execution_code varchar(20) NOT NULL,
 approval_reference text NOT NULL,
 initial_price_date date NOT NULL,
 initial_close numeric NOT NULL CHECK(initial_close>0),
 initial_capital numeric NOT NULL CHECK(initial_capital=initial_close*1.5),
 current_capital numeric NOT NULL CHECK(current_capital=initial_capital),
 -- Initial balance only. This table cannot masquerade as a settled broker capital ledger.
 reference_price numeric NOT NULL CHECK(reference_price>0),
 reference_price_time timestamp,
 reference_price_kind text NOT NULL DEFAULT 'PRIOR_KRX_DAILY_CLOSE',
 next_quantity bigint NOT NULL CHECK(next_quantity>=0),
 preparation_status text NOT NULL DEFAULT 'NO_SEND_NOT_SUBMITTABLE'
   CHECK(preparation_status='NO_SEND_NOT_SUBMITTABLE'),
 send_enabled boolean NOT NULL DEFAULT false CHECK(NOT send_enabled),
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((direction='LONG' AND execution_code='0193T0') OR
       (direction='SHORT' AND execution_code='0197X0')),
 CHECK(next_quantity=floor(current_capital/reference_price)),
 CHECK(strategy_id IN ('FV3008243','FV3008241','FV3009185','FV3009201','FV3009187',
 'FV3009203','FV3008227','FV3008225','FV3008211','FV3008209','FV3008084',
 'FV3005688','FV3005672','FV3004728','FV3004712'))
);
CREATE TABLE IF NOT EXISTS flow_v3_live_preparation_intent (
 preparation_intent_id bigserial PRIMARY KEY,
 strategy_id varchar(20) NOT NULL REFERENCES flow_v3_live_preparation(strategy_id),
 event_id bigint NOT NULL REFERENCES flow_v3_runtime_entry_event(event_id),
 paper_trade_id bigint REFERENCES flow_v3_paper_trade(paper_trade_id),
 entry_event_key varchar(300) NOT NULL,
 status text NOT NULL CHECK(status IN ('BLOCKED_NO_SEND','BLOCKED_MAPPING','BLOCKED_REFERENCE','BLOCKED_CAPITAL')),
 execution_code varchar(20) NOT NULL,
 reference_price numeric,
 reference_price_time timestamp,
 proposed_quantity bigint CHECK(proposed_quantity>=0),
 reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(strategy_id,entry_event_key), UNIQUE(event_id)
);
COMMENT ON TABLE flow_v3_live_preparation IS
 'Approved candidates INITIAL capital snapshot only, not broker settled capital or SEND authorization';
COMMENT ON TABLE flow_v3_live_preparation_intent IS
 'Non-submittable observation ledger, excluded from shared live order request/claim queues';
COMMIT;
