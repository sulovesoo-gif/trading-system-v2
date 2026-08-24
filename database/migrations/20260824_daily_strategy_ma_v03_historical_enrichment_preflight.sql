-- SELECT-ONLY preflight for Historical Enrichment B.
-- Execute immediately before the separately approved guarded UPDATE.
WITH canonical AS (
    SELECT p.*
      FROM daily_strategy_paper_trade p
      JOIN daily_strategy_master m ON m.strategy_id = p.strategy_id
     WHERE m.strategy_role = 'CANONICAL'
       AND m.day20_enabled
       AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
       AND p.data_segment = 'POST_LISTING_ACTUAL'
       AND p.return_source = '실제상품'
), classified AS (
    SELECT *,
           brake_triggered
             AND exit_reason = 'DAY20'
             AND brake_trigger_time IS NOT NULL
             AND paper_exit_time IS NOT NULL
             AND paper_exit_price IS NOT NULL AS day20_applied_proven,
           NOT brake_triggered AS day20_not_applied_proven,
           normal_exit_date IS NOT NULL AS normal_closed_proven,
           NULLIF(source_detail ->> 'normal_exit_time', '') IS NOT NULL
             AND NULLIF(source_detail ->> 'normal_exit_price', '') IS NOT NULL AS normal_exact_in_detail,
           exit_reason = 'MA_CROSS'
             AND paper_exit_time IS NOT NULL
             AND paper_exit_price IS NOT NULL AS normal_exact_from_actual_exit
      FROM canonical
)
SELECT count(*) = 2680 AS target_rows_exact,
       count(*) FILTER (WHERE day20_applied_proven) = 947 AS day20_applied_true_exact,
       count(*) FILTER (WHERE day20_not_applied_proven) = 1733 AS day20_applied_false_exact,
       count(*) FILTER (WHERE NOT day20_applied_proven AND NOT day20_not_applied_proven) = 0
           AS day20_application_unambiguous,
       count(*) FILTER (WHERE day20_applied_proven) = 947 AS day20_exit_time_price_exact,
       count(*) FILTER (WHERE normal_closed_proven) = 1680 AS normal_tracking_closed_exact,
       count(*) FILTER (WHERE NOT normal_closed_proven) = 1000 AS normal_tracking_open_exact,
       count(*) FILTER (WHERE normal_exact_in_detail OR normal_exact_from_actual_exit) = 1581
           AS normal_time_price_exact,
       count(*) FILTER (WHERE normal_closed_proven
                          AND NOT normal_exact_in_detail
                          AND NOT normal_exact_from_actual_exit) = 99
           AS date_only_normal_must_remain_null,
       count(*) FILTER (WHERE day20_enabled_at_entry IS NOT NULL
                          OR day20_applied IS NOT NULL
                          OR day20_exit_time IS NOT NULL
                          OR day20_exit_price IS NOT NULL
                          OR normal_tracking_status IS NOT NULL
                          OR normal_exit_time IS NOT NULL
                          OR normal_exit_price IS NOT NULL
                          OR normal_return_pct IS NOT NULL
                          OR normal_fixed_basis_pnl IS NOT NULL
                          OR day20_delta_return_pct IS NOT NULL
                          OR day20_delta_fixed_basis_pnl IS NOT NULL) = 0
           AS v03_target_columns_all_unpopulated
  FROM classified;

-- Source provenance; 215 legacy rows explicitly say missing execution detail
-- was not fabricated. They are never a source for new normal time/price.
WITH canonical AS (
    SELECT p.*
      FROM daily_strategy_paper_trade p
      JOIN daily_strategy_master m ON m.strategy_id = p.strategy_id
     WHERE m.strategy_role = 'CANONICAL'
       AND m.day20_enabled
       AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
       AND p.data_segment = 'POST_LISTING_ACTUAL'
       AND p.return_source = '실제상품'
)
SELECT source_system,
       count(*) AS rows,
       count(*) FILTER (WHERE source_detail ->> 'note' LIKE '%not fabricated%')
           AS explicit_nonfabrication_rows
  FROM canonical
 GROUP BY source_system
 ORDER BY source_system;
