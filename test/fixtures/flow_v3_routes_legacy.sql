-- Isolated test database ONLY. Contract columns mirror the inspected production schema.
CREATE TABLE flow_v3_strategy_master(strategy_id varchar(20) PRIMARY KEY,stock_code text,direction text,
 execution_code text,is_enabled char(1) DEFAULT 'Y');
CREATE TABLE flow_v3_paper_trade(paper_trade_id bigint PRIMARY KEY,marker text);
CREATE TABLE flow_v3_strategy_operation(operation_id bigserial PRIMARY KEY,
 strategy_id varchar(20) REFERENCES flow_v3_strategy_master,operation_status varchar(10) NOT NULL
 CHECK(operation_status IN ('PAPER','LIVE')),allocated_amount numeric NOT NULL CHECK(allocated_amount>=0),
 capital_epoch_no integer NOT NULL CHECK(capital_epoch_no>=0),effective_from timestamp NOT NULL,effective_to timestamp,
 change_reason text NOT NULL,changed_by text NOT NULL,memo text,created_at timestamp DEFAULT now(),
 CHECK(effective_to IS NULL OR effective_to>effective_from));
CREATE UNIQUE INDEX ux_flow_v3_strategy_operation_current ON flow_v3_strategy_operation(strategy_id) WHERE effective_to IS NULL;
CREATE TABLE flow_v3_runtime_entry_event(event_id bigint PRIMARY KEY,strategy_id varchar(20) REFERENCES flow_v3_strategy_master,
 entry_event_key varchar(300),paper_trade_id bigint REFERENCES flow_v3_paper_trade,entry_signal_time timestamp,
 created_at timestamp,stock_code text,direction text,execution_code text,exit_policy_code text,entry_family_code text,
 exit_fast_period integer,exit_slow_period integer);
CREATE TABLE flow_v3_live_trade(live_trade_id bigserial PRIMARY KEY,strategy_id varchar(20) REFERENCES flow_v3_strategy_master,
 paper_trade_id bigint REFERENCES flow_v3_paper_trade,operation_id bigint REFERENCES flow_v3_strategy_operation,
 trade_status text NOT NULL DEFAULT 'OPEN' CHECK(trade_status IN ('OPEN','CLOSED','CANCELLED','SKIPPED')),
 entry_signal_time timestamp,entry_order_ref text,entry_fill_time timestamp,entry_fill_avg_price numeric,entry_quantity numeric,
 exit_signal_time timestamp,exit_order_ref text,exit_fill_time timestamp,exit_fill_avg_price numeric,exit_quantity numeric,
 buy_fee numeric,sell_fee numeric,sell_tax numeric,other_cost numeric,gross_realized_pnl numeric,
 net_realized_pnl numeric,net_return_pct numeric,broker_source text,broker_detail jsonb,updated_at timestamp DEFAULT now());
CREATE TABLE flow_v3_live_checkpoint_allocation(broker_order_id uuid,checkpoint_version bigint,live_trade_id bigint,
 stock_code text,side text,delta_quantity bigint,delta_amount numeric,broker_event_time timestamp NOT NULL,
 PRIMARY KEY(broker_order_id,checkpoint_version));
CREATE TABLE flow_v3_minute_state(stock_code text,bar_time timestamp,is_complete boolean,flow_crosses jsonb,velocity_crosses jsonb);
CREATE TABLE flow_v3_runtime_cursor(stock_code text,last_bar_time timestamp);
CREATE TABLE broker_shared_cost_snapshot(trade_date date,execution_stock_code text,status text);
CREATE TABLE broker_shared_cost_allocation(trade_date date,execution_stock_code text,family text,live_trade_id bigint,
 side text,fill_notional numeric,buy_fee numeric,sell_fee numeric,sell_tax numeric,other_cost numeric);
