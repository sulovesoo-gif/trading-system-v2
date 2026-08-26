\set ON_ERROR_STOP on

SELECT data_axis, count(*) AS path_count
  FROM minute_ma_path
 GROUP BY data_axis
 ORDER BY data_axis;

SELECT count(*) AS strategy_count
  FROM minute_ma_strategy_master
 WHERE is_enabled = 'Y';

SELECT count(*) AS path_count,
       count(*) FILTER (WHERE right(data_axis, 10) = '_AFTERNOON') AS afternoon_path_count
  FROM minute_ma_path
 WHERE is_enabled = 'Y';

SELECT operation_status, count(*) AS current_operation_count
  FROM minute_ma_operation
 WHERE effective_to IS NULL
 GROUP BY operation_status
 ORDER BY operation_status;

SELECT count(*) AS paper_capital_count,
       count(*) FILTER (
         WHERE current_capital <> initial_capital + cumulative_realized_pnl
       ) AS paper_capital_invariant_errors
  FROM minute_ma_paper_capital;

SELECT count(*) AS duplicate_paths
  FROM (
    SELECT minute_strategy_id, data_axis
      FROM minute_ma_path
     GROUP BY minute_strategy_id, data_axis
    HAVING count(*) > 1
  ) duplicates;

SELECT count(*) AS orphan_paths
  FROM minute_ma_path p
  LEFT JOIN minute_ma_strategy_master s USING (minute_strategy_id)
 WHERE s.minute_strategy_id IS NULL;

SELECT send_enabled
  FROM minute_ma_send_profile
 WHERE profile_code = 'MINUTE_MA_LIVE_SEND';
