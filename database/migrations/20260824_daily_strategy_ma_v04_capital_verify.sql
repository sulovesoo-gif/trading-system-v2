-- Read-only V0.4 verification.
SELECT count(*) AS compound_capital_invariant_violations
  FROM daily_strategy_compound_capital
 WHERE strategy_compound_capital <> epoch_initial_capital + cumulative_net_realized_pnl;

SELECT count(*) AS duplicate_live_trade_settlements
  FROM (
    SELECT live_trade_id FROM daily_strategy_live_capital_settlement
     GROUP BY live_trade_id HAVING count(*) > 1
  ) duplicate_settlement;

SELECT count(*) AS settlement_epoch_orphans
  FROM daily_strategy_live_capital_settlement s
  LEFT JOIN daily_strategy_compound_capital c
    ON c.strategy_id=s.strategy_id AND c.capital_epoch_no=s.capital_epoch_no
 WHERE c.strategy_id IS NULL;

SELECT count(*) AS retryable_skip_violations
  FROM daily_strategy_live_entry_skip
 WHERE retry_allowed IS DISTINCT FROM FALSE;

SELECT count(*) AS duplicate_skip_signal_violations
  FROM (
    SELECT strategy_id,signal_event_key FROM daily_strategy_live_entry_skip
     GROUP BY strategy_id,signal_event_key HAVING count(*) > 1
  ) duplicate_skip;

SELECT count(*) AS live_trade_without_exactly_once_settlement
  FROM daily_strategy_live_trade l
 WHERE l.trade_status='CLOSED'
   AND l.live_trade_key IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM daily_strategy_live_capital_settlement s WHERE s.live_trade_id=l.live_trade_id);

SELECT (SELECT count(*) FROM daily_strategy_operation) AS operation_rows,
       (SELECT count(*) FROM daily_strategy_paper_trade) AS paper_rows,
       (SELECT count(*) FROM live_broker_order) AS broker_order_rows,
       (SELECT count(*) FROM live_broker_fill) AS broker_fill_rows;
