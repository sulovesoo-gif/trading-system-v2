-- Future chunks only; no RAW/bar writes, no drop_chunks, no legacy chunk split.
-- Execute in a transaction using the maintenance CLI (or psql --single-transaction).
SET LOCAL lock_timeout = '1s';
SET LOCAL statement_timeout = '120s';
DO $$
BEGIN
    IF (SELECT count(*) FROM timescaledb_information.dimensions
        WHERE hypertable_schema='public'
          AND hypertable_name='raw_minute_ma_integrated_execution') <> 1
       OR NOT EXISTS (
        SELECT 1 FROM timescaledb_information.dimensions
        WHERE hypertable_schema='public'
          AND hypertable_name='raw_minute_ma_integrated_execution'
          AND column_name='received_at' AND column_type::text='timestamp without time zone')
    THEN
        RAISE EXCEPTION 'Unexpected INTEGRATED RAW hypertable dimension; no change';
    END IF;
    PERFORM set_chunk_time_interval('public.raw_minute_ma_integrated_execution', INTERVAL '1 day');
END $$;
