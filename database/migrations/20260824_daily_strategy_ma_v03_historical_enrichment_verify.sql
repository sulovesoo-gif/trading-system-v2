-- Read-only verification for guarded Historical Enrichment B.
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
SELECT count(*) = 2680 AS canonical_rows,
       count(*) FILTER (WHERE day20_enabled_at_entry) = 2680 AS day20_enabled_at_entry_exact,
       count(*) FILTER (WHERE day20_applied) = 947 AS day20_applied_true_exact,
       count(*) FILTER (WHERE day20_applied = FALSE) = 1733 AS day20_applied_false_exact,
       count(*) FILTER (WHERE day20_exit_time IS NOT NULL
                          AND day20_exit_price IS NOT NULL) = 947 AS day20_exit_exact,
       count(*) FILTER (WHERE normal_tracking_status = 'CLOSED') = 1680 AS normal_closed_exact,
       count(*) FILTER (WHERE normal_tracking_status = 'OPEN') = 1000 AS normal_open_exact,
       count(*) FILTER (WHERE normal_exit_time IS NOT NULL
                          AND normal_exit_price IS NOT NULL) = 1581 AS normal_execution_exact,
       count(*) FILTER (WHERE normal_exit_date IS NOT NULL
                          AND normal_exit_time IS NULL
                          AND normal_exit_price IS NULL) = 99 AS date_only_normal_not_fabricated,
       count(*) FILTER (WHERE normal_return_pct IS NULL
                          AND normal_fixed_basis_pnl IS NULL
                          AND day20_delta_return_pct IS NULL
                          AND day20_delta_fixed_basis_pnl IS NULL) = 2680
           AS calculated_metrics_still_held
  FROM canonical;
