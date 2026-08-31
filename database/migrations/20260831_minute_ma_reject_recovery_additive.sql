-- Minute MA explicit broker rejection evidence. Additive and Minute-specific.
BEGIN;

CREATE TABLE IF NOT EXISTS minute_ma_live_broker_submit_attempt (
  broker_order_id UUID PRIMARY KEY REFERENCES live_broker_order(broker_order_id),
  order_request_id UUID NOT NULL UNIQUE REFERENCES live_order_request(order_request_id),
  intent_id UUID NOT NULL REFERENCES minute_ma_live_intent(intent_id),
  kis_tr_id VARCHAR(16) NOT NULL,
  kis_endpoint TEXT NOT NULL,
  attempted_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS minute_ma_live_broker_rejection (
  broker_order_id UUID PRIMARY KEY REFERENCES live_broker_order(broker_order_id),
  order_request_id UUID NOT NULL UNIQUE REFERENCES live_order_request(order_request_id),
  intent_id UUID NOT NULL REFERENCES minute_ma_live_intent(intent_id),
  response_code VARCHAR(64),
  response_message TEXT NOT NULL,
  kis_tr_id VARCHAR(16) NOT NULL,
  kis_endpoint TEXT NOT NULL,
  rejected_at TIMESTAMP NOT NULL,
  response_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_type VARCHAR(48) NOT NULL CHECK (evidence_type IN (
    'KIS_RESPONSE','SYSTEMD_JOURNAL_CORRECTION')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_minute_ma_live_rejection_time
  ON minute_ma_live_broker_rejection(rejected_at,broker_order_id);
CREATE INDEX IF NOT EXISTS ix_minute_ma_live_submit_attempt_time
  ON minute_ma_live_broker_submit_attempt(attempted_at,broker_order_id);

COMMIT;
