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

-- Official names must be canonical-only; history views retain the former
-- all-lineage populations for audit and comparison.
WITH view_rows AS (
    SELECT 'official_current_operation'::text AS view_name, o.strategy_id
      FROM vw_daily_strategy_current_operation o
    UNION ALL
    SELECT 'history_current_operation', o.strategy_id
      FROM vw_daily_strategy_current_operation_history o
    UNION ALL
    SELECT 'official_dashboard', d.strategy_id
      FROM vw_daily_strategy_dashboard d
    UNION ALL
    SELECT 'history_dashboard', d.strategy_id
      FROM vw_daily_strategy_dashboard_history d
    UNION ALL
    SELECT 'official_performance_all', p.strategy_id
      FROM vw_daily_strategy_performance_all p
    UNION ALL
    SELECT 'history_performance_all', p.strategy_id
      FROM vw_daily_strategy_performance_all_history p
    UNION ALL
    SELECT 'official_recent10', r.strategy_id
      FROM vw_daily_strategy_recent10 r
    UNION ALL
    SELECT 'history_recent10', r.strategy_id
      FROM vw_daily_strategy_recent10_history r
)
SELECT view_name,
       count(*) AS rows,
       count(DISTINCT v.strategy_id) AS strategy_count,
       count(*) FILTER (WHERE m.strategy_role = 'CANONICAL' AND m.is_enabled = 'Y') AS canonical_rows,
       count(*) FILTER (WHERE m.strategy_role = 'LEGACY_BRAKE_COMPARISON') AS legacy_rows
  FROM view_rows v
  JOIN daily_strategy_master m ON m.strategy_id = v.strategy_id
 GROUP BY view_name
 ORDER BY view_name;

SELECT count(*) AS official_live_exposure_rows,
       count(*) AS canonical_only_by_definition
  FROM vw_daily_strategy_live_exposure;

SELECT count(*) AS official_paper_live_audit_rows,
       count(*) FILTER (WHERE m.strategy_role = 'LEGACY_BRAKE_COMPARISON') AS legacy_rows_must_be_zero
  FROM vw_daily_strategy_paper_live_audit a
  JOIN daily_strategy_master m ON m.strategy_id = a.strategy_id;

SELECT count(*) AS official_dominance_rows,
       count(*) FILTER (WHERE a.strategy_role = 'LEGACY_BRAKE_COMPARISON'
                          OR b.strategy_role = 'LEGACY_BRAKE_COMPARISON') AS legacy_pair_rows_must_be_zero
  FROM vw_daily_strategy_latest_dominance d
  JOIN daily_strategy_master a ON a.strategy_id = d.strategy_a_id
  JOIN daily_strategy_master b ON b.strategy_id = d.strategy_b_id;

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
