-- Read-only V0.3 migration and provenance verification.
-- The whole historical PAPER ledger (8,778 rows at the audited point) is not
-- the V0.3 canonical actual-product subset. Verify both separately.

SELECT count(*) = 4800 AS master_history_unchanged,
       count(*) FILTER (WHERE brake_type = 'DAY20') = 2400 AS day20_source_unchanged,
       count(*) FILTER (WHERE brake_type = 'NONE') = 2400 AS none_source_unchanged
  FROM daily_strategy_master;

SELECT count(*) = 4800 AS operation_history_unchanged,
       count(DISTINCT strategy_id) = 4800 AS operation_strategy_coverage_unchanged,
       count(*) FILTER (WHERE effective_to IS NULL) = 4800 AS current_operation_rows_unchanged
  FROM daily_strategy_operation;

SELECT count(*) = 8778 AS paper_history_unchanged,
       count(*) FILTER (WHERE trade_status = 'CLOSED') = 7068 AS closed_history_unchanged,
       count(*) FILTER (WHERE trade_status = 'OPEN') = 1710 AS open_history_unchanged,
       count(*) FILTER (WHERE brake_triggered) = 968 AS brake_history_unchanged
  FROM daily_strategy_paper_trade;

SELECT strategy_role, is_enabled, day20_enabled, count(*)
  FROM daily_strategy_master
 GROUP BY strategy_role, is_enabled, day20_enabled
 ORDER BY strategy_role, is_enabled, day20_enabled;

-- V0.3 historical evidence: intentionally not the full PAPER ledger.
SELECT count(*) AS canonical_actual_product_trades,
       count(*) FILTER (WHERE t.trade_status = 'CLOSED') AS closed,
       count(*) FILTER (WHERE t.trade_status = 'OPEN') AS open,
       count(*) FILTER (WHERE t.brake_triggered) AS day20_triggered
  FROM daily_strategy_paper_trade t
  JOIN daily_strategy_master m ON m.strategy_id = t.strategy_id
 WHERE m.brake_type = 'DAY20'
   AND t.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
   AND t.data_segment = 'POST_LISTING_ACTUAL'
   AND t.return_source = '실제상품';

SELECT count(*) = 2400 AS canonical_runtime_rows,
       count(*) FILTER (WHERE operation_status = 'LIVE') AS canonical_live_operation_rows,
       count(*) FILTER (WHERE operation_status = 'PAPER') AS canonical_paper_operation_rows
  FROM vw_daily_strategy_v03_runtime;

SELECT count(*) = 2400 AS legacy_comparison_rows
  FROM vw_daily_strategy_v03_legacy_brake_comparison;

SELECT count(*) AS canonical_identity_duplicates
  FROM (
        SELECT signal_code, execution_code, direction,
               entry_fast_ma, entry_slow_ma, exit_fast_ma, exit_slow_ma,
               COALESCE(trend_ma, 0)
          FROM daily_strategy_master
         WHERE strategy_role = 'CANONICAL' AND is_enabled = 'Y'
         GROUP BY signal_code, execution_code, direction,
                  entry_fast_ma, entry_slow_ma, exit_fast_ma, exit_slow_ma,
                  COALESCE(trend_ma, 0)
        HAVING count(*) > 1
  ) duplicate_identity;
