-- FLOW RAW one-day audit.
-- receive_sequence is connection-global per websocket data frame.  It is not
-- valid to interpret missing integers inside one TR/symbol table as source gaps.

--name: FLOW_RAW_TIMESTAMP_SCHEMA
SELECT table_name, column_name, data_type, datetime_precision
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('raw_flow_execution','raw_flow_program','raw_flow_orderbook_5s')
  AND column_name IN ('source_event_time','received_at','receive_sequence','event_index')
ORDER BY table_name, ordinal_position;

--name: FLOW_RAW_COUNTS
SELECT 'EXECUTION' AS source, stock_code, count(*) AS row_count,
       min(source_event_time) AS first_source_event_time,
       max(source_event_time) AS last_source_event_time
FROM raw_flow_execution
GROUP BY stock_code
UNION ALL
SELECT 'PROGRAM', stock_code, count(*), min(source_event_time), max(source_event_time)
FROM raw_flow_program
GROUP BY stock_code
UNION ALL
SELECT 'ORDERBOOK_5S', stock_code, count(*), min(source_event_time), max(source_event_time)
FROM raw_flow_orderbook_5s
GROUP BY stock_code
ORDER BY source, stock_code;

--name: FLOW_SEQUENCE_CONTRACT
SELECT
  'CONNECTION_GLOBAL_WEBSOCKET_DATA_FRAME' AS receive_sequence_scope,
  'PAYLOAD_RECORD_INDEX' AS event_index_scope,
  'NOT_A_PER_TR_OR_PER_SYMBOL_GAP_CONTRACT' AS missing_integer_interpretation;

--name: FLOW_MESSAGE_FANOUT_QUALITY
WITH event_raw AS (
  SELECT connection_id, receive_sequence, event_index, tr_id
  FROM raw_flow_execution
  UNION ALL
  SELECT connection_id, receive_sequence, event_index, tr_id
  FROM raw_flow_program
), per_message AS (
  SELECT connection_id, receive_sequence, tr_id,
         count(*) AS row_count, min(event_index) AS min_event_index,
         max(event_index) AS max_event_index,
         count(DISTINCT event_index) AS distinct_event_index_count
  FROM event_raw
  GROUP BY connection_id, receive_sequence, tr_id
)
SELECT count(*) AS stored_message_count,
       count(*) FILTER (WHERE row_count > 1) AS multi_record_message_count,
       max(row_count) AS max_records_per_message,
       count(*) FILTER (
         WHERE min_event_index <> 0
            OR distinct_event_index_count <> row_count
            OR max_event_index <> row_count - 1
       ) AS event_index_invariant_failures
FROM per_message;

--name: FLOW_DURABLE_QUALITY_FLAGS
SELECT source,
       sum(row_count) AS row_count,
       sum(duplicate_count) AS duplicate_count,
       sum(source_gap_count) AS source_gap_count,
       sum(time_regression_count) AS time_regression_count,
       sum(reconnect_count) AS reconnect_count
FROM (
  SELECT 'EXECUTION' AS source, count(*) AS row_count,
         count(*) FILTER (WHERE duplicate_flag) AS duplicate_count,
         count(*) FILTER (WHERE source_gap_flag) AS source_gap_count,
         count(*) FILTER (WHERE event_time_regression_flag) AS time_regression_count,
         count(*) FILTER (WHERE reconnect_flag) AS reconnect_count
  FROM raw_flow_execution
  UNION ALL
  SELECT 'PROGRAM', count(*),
         count(*) FILTER (WHERE duplicate_flag),
         count(*) FILTER (WHERE source_gap_flag),
         count(*) FILTER (WHERE event_time_regression_flag),
         count(*) FILTER (WHERE reconnect_flag)
  FROM raw_flow_program
  UNION ALL
  SELECT 'ORDERBOOK_5S', count(*),
         count(*) FILTER (WHERE duplicate_flag),
         count(*) FILTER (WHERE source_gap_flag),
         count(*) FILTER (WHERE event_time_regression_flag),
         count(*) FILTER (WHERE reconnect_flag)
  FROM raw_flow_orderbook_5s
) quality
GROUP BY source
ORDER BY source;

--name: FLOW_L1_COUNTS
SELECT interval_seconds, stock_code, count(*) AS row_count,
       count(*) FILTER (WHERE is_complete) AS complete_count,
       min(bucket_start) AS first_bucket,
       max(bucket_start) AS last_bucket
FROM flow_bar
GROUP BY interval_seconds, stock_code
ORDER BY interval_seconds, stock_code;
