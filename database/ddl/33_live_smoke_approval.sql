-- 7C approval records are separate from GLOBAL_TRADE_YN and general LIVE mode.
-- Do not apply to production or create an approval without a separate approval.
CREATE TABLE live_smoke_approval (
    approval_id UUID PRIMARY KEY,
    phase VARCHAR(32) NOT NULL CHECK (phase = '7C-1'),
    strategy_instance_id VARCHAR(120) NOT NULL,
    active_stock_code VARCHAR(32) NOT NULL,
    allowed_date DATE NOT NULL,
    allowed_time_from TIME NOT NULL,
    allowed_time_to TIME NOT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'NOT_APPROVED',
    broker_state VARCHAR(40) NOT NULL DEFAULT 'NOT_SENT',
    broker_idempotency_key CHAR(64) NOT NULL UNIQUE,
    consumed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (allowed_time_from < allowed_time_to),
    CHECK (status IN ('NOT_APPROVED', 'APPROVED_FOR_ONE_SUBMIT', 'CONSUMED'))
);

CREATE TABLE live_smoke_approval_audit (
    audit_id BIGSERIAL PRIMARY KEY,
    approval_id UUID NOT NULL REFERENCES live_smoke_approval(approval_id),
    event_type VARCHAR(64) NOT NULL,
    event_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Durable one-submit compare-and-swap contract used immediately before send:
-- UPDATE live_smoke_approval
--    SET status='CONSUMED', consumed_at=CURRENT_TIMESTAMP
--  WHERE approval_id=:approval_id
--    AND status='APPROVED_FOR_ONE_SUBMIT'
--    AND broker_idempotency_key=:key
-- RETURNING *;
-- Timeout/ACK loss preserves status=CONSUMED and sets
-- broker_state='UNKNOWN_BROKER_STATE'; recovery may lookup but never resend.
