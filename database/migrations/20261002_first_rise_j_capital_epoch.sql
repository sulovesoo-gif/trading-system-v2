-- V1.5: additive FIRST_RISE-only epoch/config/realized-event ownership.
-- No total allocation, no common_code update, no rewrite of existing trades.
BEGIN;
CREATE TABLE first_rise_j_runtime_day (
    business_date date PRIMARY KEY,
    config_row jsonb NOT NULL,
    config_error text,
    loaded_at timestamp NOT NULL
);
CREATE TABLE first_rise_j_capital_epoch (
    epoch_id uuid PRIMARY KEY,
    epoch_no bigint NOT NULL UNIQUE CHECK (epoch_no > 0),
    effective_business_date date NOT NULL UNIQUE,
    effective_at timestamp NOT NULL,
    ended_at timestamp,
    start_slot_amount numeric NOT NULL CHECK (start_slot_amount > 0 AND trunc(start_slot_amount)=start_slot_amount),
    initial_step_amount numeric NOT NULL CHECK (initial_step_amount > 0),
    initial_max_amount numeric NOT NULL,
    slot_step_amount numeric NOT NULL CHECK (slot_step_amount > 0 AND trunc(slot_step_amount)=slot_step_amount),
    max_slot_amount numeric NOT NULL CHECK (max_slot_amount >= start_slot_amount AND trunc(max_slot_amount)=max_slot_amount),
    realized_net_pnl numeric NOT NULL DEFAULT 0,
    compound_reference numeric GENERATED ALWAYS AS (start_slot_amount + realized_net_pnl) STORED,
    common_slot_amount numeric GENERATED ALWAYS AS (
        least(max_slot_amount,start_slot_amount + greatest(0,floor(realized_net_pnl / slot_step_amount))*slot_step_amount)
    ) STORED,
    revision bigint NOT NULL DEFAULT 0,
    CHECK (ended_at IS NULL OR ended_at >= effective_at)
);
CREATE UNIQUE INDEX first_rise_j_one_active_epoch ON first_rise_j_capital_epoch((1)) WHERE ended_at IS NULL;
CREATE TABLE first_rise_j_epoch_rule_day (
    business_date date PRIMARY KEY REFERENCES first_rise_j_runtime_day(business_date),
    epoch_id uuid NOT NULL REFERENCES first_rise_j_capital_epoch(epoch_id),
    slot_step_amount numeric NOT NULL CHECK (slot_step_amount > 0),
    max_slot_amount numeric NOT NULL CHECK (max_slot_amount > 0),
    UNIQUE(epoch_id,business_date)
);
CREATE TABLE first_rise_j_capital_binding (
    trade_id uuid PRIMARY KEY,
    epoch_id uuid NOT NULL,
    entry_business_date date NOT NULL,
    stock_code varchar(16) NOT NULL,
    entry_sizing_evidence jsonb NOT NULL,
    created_at timestamp NOT NULL,
    FOREIGN KEY(epoch_id,entry_business_date)
        REFERENCES first_rise_j_epoch_rule_day(epoch_id,business_date),
    UNIQUE(trade_id,epoch_id)
);
CREATE TABLE first_rise_j_realized_event (
    event_key text PRIMARY KEY,
    trade_id uuid NOT NULL,
    epoch_id uuid NOT NULL,
    actual_net_pnl_delta numeric NOT NULL,
    settled_at timestamp NOT NULL,
    evidence jsonb NOT NULL,
    FOREIGN KEY(trade_id,epoch_id) REFERENCES first_rise_j_capital_binding(trade_id,epoch_id)
);
COMMENT ON TABLE first_rise_j_capital_epoch IS
 'Actual realized PnL reference, NOT an account cash balance or total strategy allocation.';
COMMENT ON TABLE first_rise_j_capital_binding IS
 'Immutable epoch owner assigned at pending ENTRY creation, before actual fill. Not a fill ledger.';
COMMIT;
