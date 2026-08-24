-- DRAFT ONLY — do not apply without a separate approval.
-- Daily MA V0.3 LIVE NO_SEND persistence.  Existing trade_status remains
-- OPEN/CLOSED/CANCELLED; lifecycle state is additive and nullable for history.
BEGIN;

DO $$
BEGIN
    IF to_regclass('public.daily_strategy_live_trade') IS NULL THEN
        RAISE EXCEPTION 'daily_strategy_live_trade is required';
    END IF;
    -- These are pre-existing shared ledgers, not recreated by this migration.
    IF to_regclass('public.live_broker_order') IS NULL
       OR to_regclass('public.live_broker_fill') IS NULL
       OR to_regclass('public.execution_fill_allocation') IS NULL
       OR to_regclass('public.execution_logical_position') IS NULL THEN
        RAISE EXCEPTION 'shared broker/ownership prerequisites are missing';
    END IF;
END $$;

ALTER TABLE daily_strategy_live_trade
    ADD COLUMN IF NOT EXISTS live_trade_key CHAR(64),
    ADD COLUMN IF NOT EXISTS ownership_id VARCHAR(160),
    ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(40),
    ADD COLUMN IF NOT EXISTS entry_intent_key CHAR(64),
    ADD COLUMN IF NOT EXISTS exit_intent_key CHAR(64);

-- Legacy rows remain NULL. New V0.3 rows are required by runtime to fill all
-- five fields; the table's historic trade_status CHECK is untouched.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_daily_strategy_live_v03_lifecycle') THEN
        ALTER TABLE daily_strategy_live_trade ADD CONSTRAINT ck_daily_strategy_live_v03_lifecycle
        CHECK (lifecycle_status IS NULL OR lifecycle_status IN (
            'PLANNED','ENTRY_PENDING','PARTIALLY_FILLED','OPEN','EXIT_PENDING',
            'CLOSED','CANCELLED','UNKNOWN_BROKER_STATE','RECONCILIATION_REQUIRED'
        ));
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_strategy_live_v03_trade_key
    ON daily_strategy_live_trade(live_trade_key) WHERE live_trade_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_strategy_live_v03_ownership
    ON daily_strategy_live_trade(ownership_id) WHERE ownership_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS daily_strategy_live_order_intent (
    intent_id UUID PRIMARY KEY,
    intent_key CHAR(64) NOT NULL UNIQUE,
    -- NULL in NO_SEND and until a real fill creates the physical LIVE trade.
    -- This prevents a prepared plan from appearing in LIVE exposure.
    live_trade_id BIGINT NULL REFERENCES daily_strategy_live_trade(live_trade_id),
    paper_trade_id BIGINT NOT NULL REFERENCES daily_strategy_paper_trade(paper_trade_id),
    strategy_id VARCHAR(20) NOT NULL REFERENCES daily_strategy_master(strategy_id),
    signal_event_key CHAR(64) NOT NULL,
    intent_type VARCHAR(8) NOT NULL CHECK (intent_type IN ('ENTRY','EXIT')),
    exit_reason VARCHAR(32) NULL CHECK (exit_reason IS NULL OR exit_reason IN ('NORMAL_EXIT','DAY20_EXIT')),
    source_event_time TIMESTAMP NOT NULL,
    requested_quantity INTEGER NOT NULL CHECK (requested_quantity > 0),
    reference_price NUMERIC(20,6) NULL CHECK (reference_price IS NULL OR reference_price > 0),
    requested_notional NUMERIC(20,2) NULL CHECK (requested_notional IS NULL OR requested_notional >= 0),
    lifecycle_status VARCHAR(40) NOT NULL CHECK (lifecycle_status IN (
        -- NO_SEND_VALIDATED is the existing BrokerOrderStatus code; this
        -- internal record never creates a live_broker_order row in NO_SEND.
        'PLANNED','NO_SEND_VALIDATED','BLOCKED','RECONCILIATION_REQUIRED',
        'SUBMITTING','ACCEPTED','PARTIALLY_FILLED','FILLED','CANCELLED','UNKNOWN_BROKER_STATE'
    )),
    validation_detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((intent_type='ENTRY' AND exit_reason IS NULL) OR
           (intent_type='EXIT' AND exit_reason IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_daily_strategy_live_intent_trade
    ON daily_strategy_live_order_intent(live_trade_id, lifecycle_status, created_at);
CREATE INDEX IF NOT EXISTS ix_daily_strategy_live_intent_paper
    ON daily_strategy_live_order_intent(paper_trade_id, lifecycle_status, created_at);

-- An internal order request is durable in NO_SEND.  It deliberately has no
-- broker_order_id or broker_order_number; those are introduced only below.
CREATE TABLE IF NOT EXISTS daily_strategy_live_order_request (
    order_request_id UUID PRIMARY KEY,
    request_key CHAR(64) NOT NULL UNIQUE,
    intent_id UUID NOT NULL UNIQUE REFERENCES daily_strategy_live_order_intent(intent_id),
    strategy_instance_id VARCHAR(160) NOT NULL,
    execution_stock_code VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL CHECK (side IN ('BUY','SELL')),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    order_type VARCHAR(64) NOT NULL,
    execution_target_time TIMESTAMP NOT NULL,
    request_status VARCHAR(40) NOT NULL CHECK (request_status IN (
        'PLANNED','NO_SEND_VALIDATED','BLOCKED','RECONCILIATION_REQUIRED',
        'READY_FOR_BROKER','CANCELLED','UNKNOWN_BROKER_STATE'
    )),
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- No row is inserted here in NO_SEND. Therefore broker ref and number cannot
-- be fabricated. This mapping exists only after a future authorized submit.
CREATE TABLE IF NOT EXISTS daily_strategy_live_broker_order_mapping (
    order_request_id UUID PRIMARY KEY REFERENCES daily_strategy_live_order_request(order_request_id),
    broker_order_id UUID NOT NULL UNIQUE REFERENCES live_broker_order(broker_order_id),
    broker_order_number VARCHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS daily_strategy_live_capital_reservation (
    reservation_id UUID PRIMARY KEY,
    intent_id UUID NOT NULL UNIQUE REFERENCES daily_strategy_live_order_intent(intent_id),
    -- Like the intent link, the physical LIVE trade is absent until actual fill.
    live_trade_id BIGINT NULL REFERENCES daily_strategy_live_trade(live_trade_id),
    reserved_amount NUMERIC(20,2) NOT NULL CHECK (reserved_amount >= 0),
    consumed_amount NUMERIC(20,2) NOT NULL DEFAULT 0 CHECK (consumed_amount >= 0),
    released_amount NUMERIC(20,2) NOT NULL DEFAULT 0 CHECK (released_amount >= 0),
    remaining_reserved_amount NUMERIC(20,2) GENERATED ALWAYS AS
        (reserved_amount - consumed_amount - released_amount) STORED,
    reservation_status VARCHAR(32) NOT NULL CHECK (reservation_status IN (
        'RESERVED','PARTIALLY_CONSUMED','CONSUMED','RELEASED'
    )),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (consumed_amount + released_amount <= reserved_amount)
);
CREATE INDEX IF NOT EXISTS ix_daily_strategy_live_reservation_trade
    ON daily_strategy_live_capital_reservation(live_trade_id, reservation_status);

COMMIT;
