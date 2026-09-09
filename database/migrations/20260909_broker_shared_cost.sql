BEGIN;
SET LOCAL lock_timeout='5s';
CREATE TABLE IF NOT EXISTS broker_shared_cost_snapshot (
 trade_date date NOT NULL,execution_stock_code varchar(20) NOT NULL,
 buy_fee numeric NOT NULL,sell_fee numeric NOT NULL,sell_tax numeric NOT NULL,other_cost numeric NOT NULL,
 broker_snapshot_at timestamp NOT NULL,status text NOT NULL,
 fingerprint text NOT NULL,confirmation_count integer NOT NULL,last_confirmed_at timestamp,
 failure_reason text,updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(trade_date,execution_stock_code),
 CHECK(buy_fee>=0 AND sell_fee>=0 AND sell_tax>=0 AND other_cost>=0)
);
CREATE TABLE IF NOT EXISTS broker_shared_cost_allocation (
 trade_date date NOT NULL,execution_stock_code varchar(20) NOT NULL,
 family text NOT NULL CHECK(family IN ('DAILY','MINUTE','FLOW')),
 live_trade_id bigint NOT NULL,side text NOT NULL CHECK(side IN ('BUY','SELL')),
 fill_notional numeric NOT NULL CHECK(fill_notional>0),
 buy_fee numeric NOT NULL,sell_fee numeric NOT NULL,sell_tax numeric NOT NULL,other_cost numeric NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(trade_date,execution_stock_code,family,live_trade_id,side),
 FOREIGN KEY(trade_date,execution_stock_code) REFERENCES broker_shared_cost_snapshot,
 CHECK(buy_fee>=0 AND sell_fee>=0 AND sell_tax>=0 AND other_cost>=0)
);
CREATE TABLE IF NOT EXISTS flow_v3_live_checkpoint_allocation (
 broker_order_id uuid NOT NULL,checkpoint_version integer NOT NULL CHECK(checkpoint_version>0),
 live_trade_id bigint NOT NULL REFERENCES flow_v3_live_trade(live_trade_id),
 stock_code varchar(20) NOT NULL CHECK(stock_code IN ('0193T0','0197X0')),
 side text NOT NULL CHECK(side IN ('BUY','SELL')),
 delta_quantity integer NOT NULL CHECK(delta_quantity>0),
 delta_amount numeric NOT NULL CHECK(delta_amount>0),
 broker_event_time timestamp NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(broker_order_id,checkpoint_version)
);
COMMENT ON TABLE broker_shared_cost_allocation IS
 'One broker product-day cost allocation across Daily/Minute/FLOW, never separate reallocation of broker totals';
COMMIT;
