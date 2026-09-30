BEGIN;
SET LOCAL TIME ZONE 'Asia/Seoul';

-- Forward-only cutover. Do not rewrite variant identities, epochs, trades,
-- historical KRX backfill, or the path recorded on any existing order.
ALTER TABLE minute_ma_real_variant
  ADD COLUMN IF NOT EXISTS forward_minute_path_id BIGINT REFERENCES minute_ma_path(minute_path_id),
  ADD COLUMN IF NOT EXISTS forward_signal_from TIMESTAMP NOT NULL DEFAULT LOCALTIMESTAMP;

DO $$
BEGIN
  IF EXISTS (
    SELECT v.real_variant_id FROM minute_ma_real_variant v
    LEFT JOIN minute_ma_path p ON p.minute_strategy_id=v.minute_strategy_id
    LEFT JOIN minute_ma_policy_path pp ON pp.minute_path_id=p.minute_path_id AND pp.is_enabled='Y'
    GROUP BY v.real_variant_id HAVING count(pp.minute_policy_path_id)<>1
  ) THEN
    RAISE EXCEPTION 'Expected exactly one official V1 signal path per REAL variant';
  END IF;
END $$;

UPDATE minute_ma_real_variant v
SET forward_minute_path_id=p.minute_path_id
FROM minute_ma_path p JOIN minute_ma_policy_path pp
  ON pp.minute_path_id=p.minute_path_id AND pp.is_enabled='Y'
WHERE p.minute_strategy_id=v.minute_strategy_id AND v.forward_minute_path_id IS NULL;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM minute_ma_real_variant v
    JOIN minute_ma_path p ON p.minute_path_id=v.forward_minute_path_id
    WHERE p.minute_strategy_id<>v.minute_strategy_id OR NOT EXISTS (
      SELECT 1 FROM minute_ma_policy_path pp
      WHERE pp.minute_path_id=p.minute_path_id AND pp.is_enabled='Y')
  ) THEN
    RAISE EXCEPTION 'Existing REAL forward linkage does not match official V1';
  END IF;
END $$;

-- Nullable permits frozen historical tools to create research-only variants.
-- Forward consumers explicitly reject missing linkage instead of choosing KRX.
COMMENT ON COLUMN minute_ma_real_variant.forward_minute_path_id IS
  'Official V1 semantic path; price source is exclusively v1_source_bars INTEGRATED cutover stream';
COMMENT ON COLUMN minute_ma_real_variant.forward_signal_from IS
  'No retrospective ENTRY or historical PAPER replay across this source-contract cutover';
COMMENT ON COLUMN minute_ma_real_live_route.minute_path_id IS
  'Preserved historical route linkage. Forward signals resolve via real_variant.forward_minute_path_id';

ALTER TABLE minute_ma_real_paper_trade
  ADD COLUMN IF NOT EXISTS entry_signal_evidence JSONB,
  ADD COLUMN IF NOT EXISTS exit_signal_evidence JSONB;

COMMIT;
