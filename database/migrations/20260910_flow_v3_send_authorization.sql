-- Independent FLOW approval. Applying this migration never enables SEND.
BEGIN;
SET LOCAL lock_timeout='5s';
CREATE TABLE IF NOT EXISTS flow_v3_send_profile (
 profile_code text PRIMARY KEY CHECK(profile_code='FLOW_V3_LIVE_SEND'),
 enabled char(1) NOT NULL DEFAULT 'N' CHECK(enabled IN ('Y','N')),
 updated_at timestamptz NOT NULL DEFAULT now(),
 updated_by text NOT NULL
);
INSERT INTO flow_v3_send_profile(profile_code,enabled,updated_by)
 VALUES('FLOW_V3_LIVE_SEND','N','DEFAULT_OFF_MIGRATION')
 ON CONFLICT(profile_code) DO NOTHING;
ALTER TABLE flow_v3_live_order DROP CONSTRAINT IF EXISTS flow_v3_live_order_send_enabled_check;
ALTER TABLE flow_v3_live_order DROP CONSTRAINT IF EXISTS flow_v3_live_order_post_attempt_count_check;
ALTER TABLE flow_v3_live_order ADD CONSTRAINT flow_v3_live_order_post_attempt_count_check
 CHECK(post_attempt_count BETWEEN 0 AND 1);
ALTER TABLE flow_v3_live_entry_release DROP CONSTRAINT IF EXISTS flow_v3_live_entry_release_post_attempt_count_check;
ALTER TABLE flow_v3_live_entry_release ADD CONSTRAINT flow_v3_live_entry_release_post_attempt_count_check
 CHECK(post_attempt_count BETWEEN 0 AND 1);
ALTER TABLE flow_v3_live_worker_status DROP CONSTRAINT IF EXISTS flow_v3_live_worker_status_send_enabled_check;
-- Keep all defaults OFF. Never arm previously prepared orders or replay events.
COMMIT;
