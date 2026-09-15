-- READ ONLY; no expensive full RAW count/hash scan.
SELECT extversion FROM pg_extension WHERE extname='timescaledb';
SELECT hypertable_schema,hypertable_name,column_name,column_type,time_interval
FROM timescaledb_information.dimensions
WHERE hypertable_schema='public' AND hypertable_name='raw_minute_ma_integrated_execution';
SELECT chunk_schema,chunk_name,
       range_start AT TIME ZONE 'UTC' AS raw_naive_start_inclusive,
       range_end AT TIME ZONE 'UTC' AS raw_naive_end_exclusive,
       pg_size_pretty(pg_total_relation_size(format('%I.%I',chunk_schema,chunk_name)::regclass)) AS size
FROM timescaledb_information.chunks
WHERE hypertable_schema='public' AND hypertable_name='raw_minute_ma_integrated_execution'
ORDER BY range_start;
SELECT pg_size_pretty(hypertable_size('public.raw_minute_ma_integrated_execution')) AS total_raw_size;
SELECT stock_code,max(bar_time) AS latest_completed_bar
FROM public.minute_ma_integrated_realtime_minute_bar GROUP BY stock_code;
