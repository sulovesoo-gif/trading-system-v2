-- Guarded inverse for Historical Enrichment B only.
-- It never deletes a master, operation, or PAPER row. Run only if no later
-- enrichment/runtime process has populated any of these historical fields.
BEGIN;

DO $$
DECLARE
    target_rows BIGINT;
    mismatched_rows BIGINT;
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
        RAISE EXCEPTION 'Enrichment rollback provenance mismatch: expected 2680, got %', target_rows;
    END IF;

    WITH expected AS (
        SELECT p.*,
               CASE
                   WHEN p.brake_triggered
                    AND p.exit_reason = 'DAY20'
                    AND p.brake_trigger_time IS NOT NULL
                    AND p.paper_exit_time IS NOT NULL
                    AND p.paper_exit_price IS NOT NULL THEN TRUE
                   WHEN NOT p.brake_triggered THEN FALSE
                   ELSE NULL
               END AS expected_day20_applied,
               CASE WHEN p.normal_exit_date IS NOT NULL THEN 'CLOSED' ELSE 'OPEN' END
                   AS expected_normal_tracking_status,
               CASE
                   WHEN p.exit_reason = 'MA_CROSS' THEN p.paper_exit_time
                   WHEN NULLIF(p.source_detail ->> 'normal_exit_time', '') IS NOT NULL
                    AND NULLIF(p.source_detail ->> 'normal_exit_price', '') IS NOT NULL
                       THEN (p.source_detail ->> 'normal_exit_time')::timestamp
                   ELSE NULL
               END AS expected_normal_exit_time,
               CASE
                   WHEN p.exit_reason = 'MA_CROSS' THEN p.paper_exit_price
                   WHEN NULLIF(p.source_detail ->> 'normal_exit_time', '') IS NOT NULL
                    AND NULLIF(p.source_detail ->> 'normal_exit_price', '') IS NOT NULL
                       THEN (p.source_detail ->> 'normal_exit_price')::numeric
                   ELSE NULL
               END AS expected_normal_exit_price
          FROM daily_strategy_paper_trade p
          JOIN daily_strategy_master m ON m.strategy_id = p.strategy_id
         WHERE m.strategy_role = 'CANONICAL'
           AND m.day20_enabled
           AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
           AND p.data_segment = 'POST_LISTING_ACTUAL'
           AND p.return_source = '실제상품'
    )
    SELECT count(*)
      INTO mismatched_rows
      FROM expected p
     WHERE p.day20_enabled_at_entry IS DISTINCT FROM TRUE
        OR p.day20_applied IS DISTINCT FROM p.expected_day20_applied
        OR p.day20_exit_time IS DISTINCT FROM CASE WHEN p.expected_day20_applied THEN p.paper_exit_time END
        OR p.day20_exit_price IS DISTINCT FROM CASE WHEN p.expected_day20_applied THEN p.paper_exit_price END
        OR p.normal_tracking_status IS DISTINCT FROM p.expected_normal_tracking_status
        OR p.normal_exit_time IS DISTINCT FROM p.expected_normal_exit_time
        OR p.normal_exit_price IS DISTINCT FROM p.expected_normal_exit_price
        OR p.normal_return_pct IS NOT NULL
        OR p.normal_fixed_basis_pnl IS NOT NULL
        OR p.day20_delta_return_pct IS NOT NULL
        OR p.day20_delta_fixed_basis_pnl IS NOT NULL;

    IF mismatched_rows <> 0 THEN
        RAISE EXCEPTION
            'Enrichment rollback refuses % rows outside the approved B shape', mismatched_rows;
    END IF;
END $$;

UPDATE daily_strategy_paper_trade p
   SET day20_enabled_at_entry = NULL,
       day20_applied = NULL,
       day20_exit_time = NULL,
       day20_exit_price = NULL,
       normal_tracking_status = NULL,
       normal_exit_time = NULL,
       normal_exit_price = NULL
  FROM daily_strategy_master m
 WHERE m.strategy_id = p.strategy_id
   AND m.strategy_role = 'CANONICAL'
   AND m.day20_enabled
   AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'
   AND p.data_segment = 'POST_LISTING_ACTUAL'
   AND p.return_source = '실제상품';

COMMIT;
