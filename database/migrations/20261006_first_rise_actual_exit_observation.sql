-- Evidence only. Do not backfill observation times from order timestamps.
BEGIN;
SET LOCAL lock_timeout='5s';
ALTER TABLE first_rise_j_live_checkpoint_allocation
    ADD COLUMN fill_observed_at timestamp;
COMMENT ON COLUMN first_rise_j_live_checkpoint_allocation.fill_observed_at IS
 'KST naive timestamp of positive cumulative fill delta observation; not exact broker execution time. Historical unknown values remain NULL.';
COMMIT;
