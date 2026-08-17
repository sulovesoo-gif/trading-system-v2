-- Intent-only LIVE runtime persistence proposal.  Do not apply to production in phase 6.

CREATE TABLE live_strategy_runtime_state (
    strategy_instance_id VARCHAR(120) PRIMARY KEY,
    runtime_status VARCHAR(32) NOT NULL,
    strategy_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE live_strategy_intent (
    intent_id UUID PRIMARY KEY,
    idempotency_key CHAR(64) NOT NULL UNIQUE,
    strategy_instance_id VARCHAR(120) NOT NULL,
    strategy_code VARCHAR(120) NOT NULL,
    strategy_version VARCHAR(32) NOT NULL,
    code_commit VARCHAR(64),
    source_decision_id UUID NOT NULL,
    intent_type VARCHAR(32) NOT NULL,
    signal_stock_code VARCHAR(32) NOT NULL,
    signal_direction VARCHAR(16) NOT NULL,
    execution_stock_code VARCHAR(32) NOT NULL,
    execution_direction VARCHAR(16) NOT NULL,
    signal_time TIMESTAMP NOT NULL,
    decision_time TIMESTAMP NOT NULL,
    execution_target_time TIMESTAMP NOT NULL,
    reason_code VARCHAR(120) NOT NULL,
    decision_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    data_quality_status VARCHAR(64) NOT NULL,
    runtime_state_before VARCHAR(32) NOT NULL,
    runtime_state_after VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_live_strategy_intent_instance_time ON live_strategy_intent(strategy_instance_id, signal_time);

CREATE TABLE live_strategy_audit (
    audit_id BIGSERIAL PRIMARY KEY,
    event_type VARCHAR(64) NOT NULL,
    strategy_instance_id VARCHAR(120) NOT NULL,
    event_time TIMESTAMP NOT NULL,
    source_decision_id UUID,
    reason VARCHAR(256) NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
