-- Read-only V0.3 migration and provenance verification.
SELECT strategy_role, is_enabled, day20_enabled, count(*)
  FROM daily_strategy_master
 GROUP BY strategy_role, is_enabled, day20_enabled
 ORDER BY strategy_role, is_enabled, day20_enabled;

-- V0.3 historical evidence: this is intentionally not the full PAPER ledger.
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
