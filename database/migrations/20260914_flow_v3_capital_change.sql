-- Additive audit only. No existing operation/capital/PAPER row is changed.
BEGIN;
CREATE TABLE IF NOT EXISTS flow_v3_live_capital_change (
    capital_change_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    operation_id bigint NOT NULL REFERENCES flow_v3_strategy_operation(operation_id),
    replacement_operation_id bigint REFERENCES flow_v3_strategy_operation(operation_id),
    strategy_id text NOT NULL REFERENCES flow_v3_strategy_master(strategy_id),
    execution_route text NOT NULL CHECK(execution_route IN ('UNDERLYING','LEVERAGE','INVERSE')),
    previous_amount numeric NOT NULL CHECK(previous_amount >= 0),
    approved_amount numeric NOT NULL CHECK(approved_amount >= 0),
    current_capital_before numeric,
    realized_net_before numeric,
    changed_at timestamp NOT NULL,
    approval_reference text NOT NULL CHECK(length(trim(approval_reference))>0),
    change_reason text NOT NULL,
    UNIQUE(strategy_id,execution_route,approval_reference)
);
COMMIT;
