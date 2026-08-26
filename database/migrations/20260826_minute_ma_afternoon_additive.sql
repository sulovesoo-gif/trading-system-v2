\set ON_ERROR_STOP on
BEGIN;

DO $$
DECLARE
  strategy_count bigint;
  base_path_count bigint;
BEGIN
  SELECT count(*) INTO strategy_count
    FROM minute_ma_strategy_master
   WHERE is_enabled = 'Y';
  SELECT count(*) INTO base_path_count
    FROM minute_ma_path
   WHERE data_axis IN (
     'KRX_CONTINUOUS', 'KRX_RESET',
     'INTEGRATED_CONTINUOUS', 'INTEGRATED_RESET');
  IF strategy_count <> 2400 OR base_path_count <> 9600 THEN
    RAISE EXCEPTION
      'AFTERNOON prerequisite failed: strategies %, base paths %',
      strategy_count, base_path_count;
  END IF;
  IF (SELECT send_enabled FROM minute_ma_send_profile
       WHERE profile_code = 'MINUTE_MA_LIVE_SEND') IS DISTINCT FROM 'N' THEN
    RAISE EXCEPTION 'Minute MA Actual SEND must remain locked';
  END IF;
END $$;

ALTER TABLE minute_ma_path
  DROP CONSTRAINT IF EXISTS minute_ma_path_data_axis_check;
ALTER TABLE minute_ma_path
  ADD CONSTRAINT minute_ma_path_data_axis_check CHECK (data_axis IN (
    'KRX_CONTINUOUS', 'KRX_RESET',
    'INTEGRATED_CONTINUOUS', 'INTEGRATED_RESET',
    'KRX_CONTINUOUS_AFTERNOON', 'KRX_RESET_AFTERNOON',
    'INTEGRATED_CONTINUOUS_AFTERNOON', 'INTEGRATED_RESET_AFTERNOON'));

INSERT INTO minute_ma_path(
  minute_strategy_id,
  path_key,
  data_axis,
  market_source,
  continuity_mode,
  session_start,
  session_end,
  historical_status
)
SELECT s.minute_strategy_id,
       s.strategy_key || '|' || a.data_axis,
       a.data_axis,
       a.market_source,
       a.continuity_mode,
       a.session_start,
       a.session_end,
       a.historical_status
  FROM minute_ma_strategy_master s
 CROSS JOIN (VALUES
   ('KRX_CONTINUOUS_AFTERNOON', 'KRX', 'CONTINUOUS', TIME '09:00', TIME '15:30', 'AVAILABLE'),
   ('KRX_RESET_AFTERNOON', 'KRX', 'RESET', TIME '09:00', TIME '15:30', 'PENDING'),
   ('INTEGRATED_CONTINUOUS_AFTERNOON', 'INTEGRATED', 'CONTINUOUS', TIME '08:00', TIME '19:59', 'PENDING'),
   ('INTEGRATED_RESET_AFTERNOON', 'INTEGRATED', 'RESET', TIME '08:00', TIME '19:59', 'PENDING')
 ) AS a(data_axis, market_source, continuity_mode, session_start, session_end, historical_status)
 WHERE s.is_enabled = 'Y'
ON CONFLICT (minute_strategy_id, data_axis) DO NOTHING;

INSERT INTO minute_ma_paper_capital(minute_path_id)
SELECT p.minute_path_id
  FROM minute_ma_path p
 WHERE right(p.data_axis, 10) = '_AFTERNOON'
ON CONFLICT DO NOTHING;

INSERT INTO minute_ma_operation(
  minute_path_id,
  operation_status,
  allocated_amount,
  capital_epoch_no,
  effective_from,
  change_reason,
  audit_reference
)
SELECT p.minute_path_id,
       'PAPER',
       0,
       0,
       CURRENT_TIMESTAMP,
       'ADMIN',
       'MINUTE_MA_AFTERNOON_INITIAL_PAPER'
  FROM minute_ma_path p
 WHERE right(p.data_axis, 10) = '_AFTERNOON'
ON CONFLICT (minute_path_id) WHERE effective_to IS NULL DO NOTHING;

DO $$
DECLARE
  strategy_count bigint;
  path_count bigint;
  afternoon_path_count bigint;
  current_operation_count bigint;
  paper_capital_count bigint;
  duplicate_count bigint;
BEGIN
  SELECT count(*) INTO strategy_count
    FROM minute_ma_strategy_master WHERE is_enabled = 'Y';
  SELECT count(*) INTO path_count
    FROM minute_ma_path WHERE is_enabled = 'Y';
  SELECT count(*) INTO afternoon_path_count
    FROM minute_ma_path
   WHERE is_enabled = 'Y'
     AND right(data_axis, 10) = '_AFTERNOON';
  SELECT count(*) INTO current_operation_count
    FROM minute_ma_operation WHERE effective_to IS NULL;
  SELECT count(*) INTO paper_capital_count FROM minute_ma_paper_capital;
  SELECT count(*) INTO duplicate_count
    FROM (
      SELECT minute_strategy_id, data_axis
        FROM minute_ma_path
       GROUP BY minute_strategy_id, data_axis
      HAVING count(*) > 1
    ) duplicates;
  IF strategy_count <> 2400
     OR path_count <> 19200
     OR afternoon_path_count <> 9600
     OR current_operation_count <> 19200
     OR paper_capital_count <> 19200
     OR duplicate_count <> 0 THEN
    RAISE EXCEPTION
      'AFTERNOON invariant failed: strategies %, paths %, afternoon %, operations %, paper capital %, duplicates %',
      strategy_count, path_count, afternoon_path_count,
      current_operation_count, paper_capital_count, duplicate_count;
  END IF;
END $$;

COMMIT;
