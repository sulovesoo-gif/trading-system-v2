BEGIN;

-- The first observed minute after startup has no preceding accumulated-volume
-- checkpoint.  Preserve unknown as NULL; never rewrite it as a false zero.
ALTER TABLE minute_ma_integrated_realtime_minute_bar
    ALTER COLUMN volume DROP NOT NULL;

COMMIT;
