-- Guarded Historical Enrichment B for Daily MA V0.3.
-- Run only after Migration A has been verified and separately approved.
-- This intentionally does not calculate normal return/P&L or DAY20 deltas.
BEGIN;

DO $$
DECLARE
    target_rows BIGINT;
    prepopulated_rows BIGINT;
BEGIN
    SELECT count(*)
      INTO target_rows
      FROM daily_strategy_paper_trade p
      JOIN daily_strategy_master m ON m.strategy_id = p.strategy_id
     WHERE m.strategy_role = 'CANONICAL'
       AND m.day20_enabled
       AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
       AND p.data_segment = 'POST_LISTING_ACTUAL'
       AND p.return_source = '실제상품';

    IF target_rows <> 2680 THEN
        RAISE EXCEPTION 'Historical enrichment provenance mismatch: expected 2680, got %', target_rows;
    END IF;

    SELECT count(*)
      INTO prepopulated_rows
      FROM daily_strategy_paper_trade p
      JOIN daily_strategy_master m ON m.strategy_id = p.strategy_id
     WHERE m.strategy_role = 'CANONICAL'
       AND m.day20_enabled
       AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
       AND p.data_segment = 'POST_LISTING_ACTUAL'
       AND p.return_source = '실제상품'
       AND (p.day20_enabled_at_entry IS NOT NULL
            OR p.day20_applied IS NOT NULL
            OR p.day20_exit_time IS NOT NULL
            OR p.day20_exit_price IS NOT NULL
            OR p.normal_tracking_status IS NOT NULL
            OR p.normal_exit_time IS NOT NULL
            OR p.normal_exit_price IS NOT NULL
            OR p.normal_return_pct IS NOT NULL
            OR p.normal_fixed_basis_pnl IS NOT NULL
            OR p.day20_delta_return_pct IS NOT NULL
            OR p.day20_delta_fixed_basis_pnl IS NOT NULL);

    IF prepopulated_rows <> 0 THEN
        RAISE EXCEPTION 'Historical enrichment refuses to overwrite % populated V0.3 rows', prepopulated_rows;
    END IF;
END $$;

WITH eligible AS (
    SELECT p.paper_trade_id,
           p.brake_triggered,
           p.brake_trigger_time,
           p.exit_reason,
           p.paper_exit_time,
           p.paper_exit_price,
           p.normal_exit_date,
           NULLIF(p.source_detail ->> 'normal_exit_time', '') AS detail_normal_exit_time,
           NULLIF(p.source_detail ->> 'normal_exit_price', '') AS detail_normal_exit_price
      FROM daily_strategy_paper_trade p
      JOIN daily_strategy_master m ON m.strategy_id = p.strategy_id
     WHERE m.strategy_role = 'CANONICAL'
       AND m.day20_enabled
       AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
       AND p.data_segment = 'POST_LISTING_ACTUAL'
       AND p.return_source = '실제상품'
)
UPDATE daily_strategy_paper_trade p
   SET day20_enabled_at_entry = TRUE,
       day20_applied = CASE
           WHEN e.brake_triggered
            AND e.exit_reason = 'DAY20'
            AND e.brake_trigger_time IS NOT NULL
            AND e.paper_exit_time IS NOT NULL
            AND e.paper_exit_price IS NOT NULL THEN TRUE
           WHEN NOT e.brake_triggered THEN FALSE
           ELSE NULL
       END,
       day20_exit_time = CASE
           WHEN e.brake_triggered
            AND e.exit_reason = 'DAY20'
            AND e.brake_trigger_time IS NOT NULL
            AND e.paper_exit_time IS NOT NULL
            AND e.paper_exit_price IS NOT NULL THEN e.paper_exit_time
           ELSE NULL
       END,
       day20_exit_price = CASE
           WHEN e.brake_triggered
            AND e.exit_reason = 'DAY20'
            AND e.brake_trigger_time IS NOT NULL
            AND e.paper_exit_time IS NOT NULL
            AND e.paper_exit_price IS NOT NULL THEN e.paper_exit_price
           ELSE NULL
       END,
       normal_tracking_status = CASE
           WHEN e.normal_exit_date IS NOT NULL THEN 'CLOSED'
           ELSE 'OPEN'
       END,
       normal_exit_time = CASE
           WHEN e.exit_reason = 'MA_CROSS' THEN e.paper_exit_time
           WHEN e.detail_normal_exit_time IS NOT NULL
            AND e.detail_normal_exit_price IS NOT NULL THEN e.detail_normal_exit_time::timestamp
           ELSE NULL
       END,
       normal_exit_price = CASE
           WHEN e.exit_reason = 'MA_CROSS' THEN e.paper_exit_price
           WHEN e.detail_normal_exit_time IS NOT NULL
            AND e.detail_normal_exit_price IS NOT NULL THEN e.detail_normal_exit_price::numeric
           ELSE NULL
       END
  FROM eligible e
 WHERE p.paper_trade_id = e.paper_trade_id;

COMMIT;
