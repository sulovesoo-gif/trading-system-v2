-- V1.8. New FIRST_RISE ownership only; no historical costs are rewritten.
BEGIN;
SET LOCAL lock_timeout='5s';
ALTER TABLE broker_shared_cost_allocation
    DROP CONSTRAINT broker_shared_cost_allocation_family_check;
ALTER TABLE broker_shared_cost_allocation ADD CONSTRAINT broker_shared_cost_allocation_family_check
    CHECK (family IN ('DAILY','MINUTE','FLOW','FIRST_RISE'));

CREATE TABLE first_rise_j_live_cost (
    cost_trade_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trade_id uuid NOT NULL UNIQUE,
    epoch_id uuid NOT NULL,
    ownership text NOT NULL DEFAULT 'FIRST_RISE' CHECK (ownership='FIRST_RISE'),
    buy_quantity bigint NOT NULL DEFAULT 0 CHECK (buy_quantity>=0),
    sell_quantity bigint NOT NULL DEFAULT 0 CHECK (sell_quantity>=0 AND sell_quantity<=buy_quantity),
    buy_amount numeric NOT NULL DEFAULT 0 CHECK (buy_amount>=0),
    sell_amount numeric NOT NULL DEFAULT 0 CHECK (sell_amount>=0),
    gross_realized_pnl numeric,
    provisional_buy_fee numeric,
    provisional_sell_fee numeric,
    provisional_sell_tax numeric,
    provisional_other_cost numeric,
    provisional_net_realized_pnl numeric,
    provisional_applied_at timestamp,
    provisional_key text UNIQUE,
    rate_evidence jsonb,
    actual_buy_fee numeric,
    actual_sell_fee numeric,
    actual_sell_tax numeric,
    actual_other_cost numeric,
    actual_cost_finalized_at timestamp,
    settlement_delta numeric,
    settlement_delta_applied_at timestamp,
    settlement_key text UNIQUE,
    final_net_realized_pnl numeric,
    FOREIGN KEY(trade_id,epoch_id) REFERENCES first_rise_j_capital_binding(trade_id,epoch_id),
    FOREIGN KEY(provisional_key) REFERENCES first_rise_j_realized_event(event_key) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(settlement_key) REFERENCES first_rise_j_realized_event(event_key) DEFERRABLE INITIALLY DEFERRED,
    CHECK (provisional_applied_at IS NULL OR (
        buy_quantity>0 AND sell_quantity=buy_quantity AND
        gross_realized_pnl IS NOT NULL AND gross_realized_pnl=sell_amount-buy_amount AND
        provisional_buy_fee IS NOT NULL AND provisional_buy_fee>=0 AND
        provisional_sell_fee IS NOT NULL AND provisional_sell_fee>=0 AND
        provisional_sell_tax IS NOT NULL AND provisional_sell_tax>=0 AND
        provisional_other_cost IS NOT NULL AND provisional_other_cost>=0 AND
        provisional_key IS NOT NULL AND rate_evidence IS NOT NULL AND
        provisional_net_realized_pnl IS NOT NULL AND
        provisional_net_realized_pnl=gross_realized_pnl-provisional_buy_fee-provisional_sell_fee-provisional_sell_tax-provisional_other_cost)),
    CHECK (settlement_delta_applied_at IS NULL OR (
        provisional_applied_at IS NOT NULL AND actual_cost_finalized_at IS NOT NULL AND
        actual_buy_fee IS NOT NULL AND actual_buy_fee>=0 AND
        actual_sell_fee IS NOT NULL AND actual_sell_fee>=0 AND
        actual_sell_tax IS NOT NULL AND actual_sell_tax>=0 AND
        actual_other_cost IS NOT NULL AND actual_other_cost>=0 AND
        settlement_key IS NOT NULL AND settlement_delta IS NOT NULL AND
        settlement_delta=provisional_buy_fee+provisional_sell_fee+provisional_sell_tax+provisional_other_cost
                         -actual_buy_fee-actual_sell_fee-actual_sell_tax-actual_other_cost AND
        final_net_realized_pnl IS NOT NULL AND
        final_net_realized_pnl=provisional_net_realized_pnl+settlement_delta))
);
-- Not broker fills: delta ownership of the existing cumulative KIS checkpoint.
CREATE TABLE first_rise_j_sell_allocation (
    order_request_id uuid NOT NULL REFERENCES live_order_request(order_request_id),
    trade_id uuid NOT NULL,
    epoch_id uuid NOT NULL,
    allocation_order integer NOT NULL CHECK(allocation_order>=0),
    planned_quantity integer NOT NULL CHECK(planned_quantity>0),
    PRIMARY KEY(order_request_id,trade_id),
    UNIQUE(order_request_id,allocation_order),
    FOREIGN KEY(trade_id,epoch_id) REFERENCES first_rise_j_capital_binding(trade_id,epoch_id)
);
CREATE TABLE first_rise_j_live_checkpoint_allocation (
    broker_order_id uuid NOT NULL REFERENCES live_broker_order(broker_order_id),
    checkpoint_version integer NOT NULL CHECK (checkpoint_version>0),
    cost_trade_id bigint NOT NULL REFERENCES first_rise_j_live_cost(cost_trade_id),
    stock_code varchar(20) NOT NULL,
    side text NOT NULL CHECK (side IN ('BUY','SELL')),
    delta_quantity integer NOT NULL CHECK (delta_quantity>0),
    delta_amount numeric NOT NULL CHECK (delta_amount>0),
    broker_event_time timestamp NOT NULL,
    PRIMARY KEY(broker_order_id,checkpoint_version,cost_trade_id)
);
CREATE INDEX first_rise_j_cost_product_day ON first_rise_j_live_checkpoint_allocation(stock_code,broker_event_time);
COMMENT ON TABLE first_rise_j_live_cost IS
 'V1.8 per-trade provisional and final costs; one FIRST_RISE strategy compound per epoch, never per-slot compounding.';
COMMENT ON COLUMN first_rise_j_capital_epoch.realized_net_pnl IS
 'V1.8 sizing aggregate: provisional actual-fill net PnL plus finalized actual-cost deltas. Evidence distinguishes PROVISIONAL and ACTUAL_COST_DELTA; not all costs are final intraday.';
COMMIT;
