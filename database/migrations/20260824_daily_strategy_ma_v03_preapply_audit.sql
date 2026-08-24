-- SELECT-ONLY pre-apply audit for Daily MA V0.3.
-- Run against the current schema before any ALTER/UPDATE/CREATE VIEW.

-- 1) Discover every operational Daily-MA view and its exact current definition.
SELECT c.relname AS view_name,
       pg_get_viewdef(c.oid, true) AS current_definition
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public'
   AND c.relkind = 'v'
   AND c.relname LIKE 'vw_daily_strategy%'
 ORDER BY c.relname;

-- 2) Current row/strategy counts and the post-role prospective split.
WITH per_view AS (
    SELECT 'vw_daily_strategy_current_operation'::text AS view_name, o.strategy_id
      FROM vw_daily_strategy_current_operation o
    UNION ALL
    SELECT 'vw_daily_strategy_dashboard', d.strategy_id
      FROM vw_daily_strategy_dashboard d
    UNION ALL
    SELECT 'vw_daily_strategy_performance_all', p.strategy_id
      FROM vw_daily_strategy_performance_all p
    UNION ALL
    SELECT 'vw_daily_strategy_recent10', r.strategy_id
      FROM vw_daily_strategy_recent10 r
    UNION ALL
    SELECT 'vw_daily_strategy_paper_live_audit', a.strategy_id
      FROM vw_daily_strategy_paper_live_audit a
)
SELECT v.view_name,
       count(*) AS current_rows,
       count(DISTINCT v.strategy_id) AS current_strategy_count,
       count(*) FILTER (WHERE m.brake_type = 'DAY20') AS prospective_canonical_rows,
       count(*) FILTER (WHERE m.brake_type = 'NONE') AS prospective_legacy_rows
  FROM per_view v
  LEFT JOIN daily_strategy_master m ON m.strategy_id = v.strategy_id
 GROUP BY v.view_name
 ORDER BY v.view_name;

SELECT 'vw_daily_strategy_live_exposure' AS view_name, count(*) AS current_rows
  FROM vw_daily_strategy_live_exposure
UNION ALL
SELECT 'vw_daily_strategy_latest_dominance', count(*)
  FROM vw_daily_strategy_latest_dominance;

-- 3) Existing in-repository view consumers are audited by rg separately.
-- Database view-to-view dependencies are shown here.
SELECT DISTINCT dependent.relname AS dependent_view,
       referenced.relname AS referenced_relation
  FROM pg_depend d
  JOIN pg_rewrite rw ON rw.oid = d.objid
  JOIN pg_class dependent ON dependent.oid = rw.ev_class
  JOIN pg_class referenced ON referenced.oid = d.refobjid
  JOIN pg_namespace n ON n.oid = dependent.relnamespace
 WHERE n.nspname = 'public'
   AND dependent.relkind = 'v'
   AND dependent.relname LIKE 'vw_daily_strategy%'
   AND referenced.relname LIKE 'vw_daily_strategy%'
   AND dependent.oid <> referenced.oid
 ORDER BY dependent.relname, referenced.relname;

-- 4) Historical-field recoverability population. This CTE deliberately uses
-- existing columns plus source_detail only; it creates no values.
WITH canonical AS (
    SELECT p.*, m.brake_type
      FROM daily_strategy_paper_trade p
      JOIN daily_strategy_master m ON m.strategy_id = p.strategy_id
     WHERE m.brake_type = 'DAY20'
       AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
       AND p.data_segment = 'POST_LISTING_ACTUAL'
       AND p.return_source = '실제상품'
), classified AS (
    SELECT *,
           brake_triggered
             AND exit_reason = 'DAY20'
             AND brake_trigger_time IS NOT NULL
             AND paper_exit_time IS NOT NULL
             AND paper_exit_price IS NOT NULL AS day20_actual_exit_proven,
           NULLIF(source_detail ->> 'normal_exit_time', '') IS NOT NULL
             AND NULLIF(source_detail ->> 'normal_exit_price', '') IS NOT NULL AS normal_exit_exact_in_detail,
           normal_exit_date IS NOT NULL AS normal_exit_date_known
      FROM canonical
)
SELECT count(*) AS canonical_rows,
       count(*) FILTER (WHERE brake_type = 'DAY20') AS day20_enabled_at_entry_proven,
       count(*) FILTER (WHERE day20_actual_exit_proven) AS day20_applied_true_proven,
       count(*) FILTER (WHERE NOT brake_triggered) AS day20_applied_false_proven,
       count(*) FILTER (WHERE day20_actual_exit_proven) AS day20_exit_time_price_exact,
       count(*) FILTER (WHERE normal_exit_exact_in_detail) AS normal_exit_time_price_exact_from_detail,
       count(*) FILTER (WHERE exit_reason = 'MA_CROSS'
                           AND paper_exit_time IS NOT NULL
                           AND paper_exit_price IS NOT NULL) AS normal_exit_time_price_exact_from_actual_ma_exit,
       count(*) FILTER (WHERE normal_exit_date_known) AS normal_tracking_closed_proven,
       count(*) FILTER (WHERE NOT normal_exit_date_known) AS normal_tracking_open_proven
  FROM classified;

-- 5) Rows explicitly marked as non-fabricated legacy execution detail must
-- retain NULL for any field whose exact timestamp or price is absent.
WITH canonical AS (
    SELECT p.*
      FROM daily_strategy_paper_trade p
      JOIN daily_strategy_master m ON m.strategy_id = p.strategy_id
     WHERE m.brake_type = 'DAY20'
       AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
       AND p.data_segment = 'POST_LISTING_ACTUAL'
       AND p.return_source = '실제상품'
)
SELECT source_system,
       count(*) AS rows,
       count(*) FILTER (WHERE source_detail ->> 'note' LIKE '%not fabricated%') AS explicit_nonfabrication_rows,
       count(*) FILTER (WHERE source_detail ? 'normal_exit_time') AS exact_normal_detail_rows
  FROM canonical
 GROUP BY source_system
 ORDER BY source_system;
