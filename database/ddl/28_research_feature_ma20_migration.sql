-- Non-destructive MA20 persistence for future COMPLETE research runs.
-- Existing feature rows and completed runs are intentionally not replayed.
ALTER TABLE research_feature ADD COLUMN IF NOT EXISTS ma20 NUMERIC(18,8);
COMMENT ON COLUMN research_feature.ma20 IS
  'Observation-only MA20 from twenty continuous completed bars; never a strategy entry or exit condition.';
