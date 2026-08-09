-- :run_id, :start_date, :end_date parameters are supplied by the research admin/query client.
-- Daily top 10
SELECT trading_date, trade_stock_code, signal_source_stock_code, strategy_code, observation_code, direction,
       closed_count, win_count, loss_count, flat_count,
       CASE WHEN closed_count = 0 THEN 0 ELSE win_count::numeric / closed_count * 100 END AS win_rate,
       realized_profit, invested_amount, invested_return_rate, capital_return_rate,
       avg_trade_return_rate, avg_holding_seconds, signal_exit_profit, session_close_profit
FROM research_performance_daily
WHERE run_id = :run_id
ORDER BY capital_return_rate DESC, realized_profit DESC
LIMIT 10;

-- Dynamic period top 10. research_performance_period is DEPRECATED.
SELECT trade_stock_code, signal_source_stock_code, strategy_code, observation_code, direction,
       SUM(closed_count) AS closed_count, SUM(win_count) AS win_count, SUM(loss_count) AS loss_count, SUM(flat_count) AS flat_count,
       CASE WHEN SUM(closed_count) = 0 THEN 0 ELSE SUM(win_count)::numeric / SUM(closed_count) * 100 END AS win_rate,
       SUM(realized_profit) AS realized_profit, SUM(invested_amount) AS invested_amount,
       COALESCE(SUM(realized_profit)/NULLIF(SUM(invested_amount),0)*100,0) AS invested_return_rate,
       SUM(realized_profit)/10000000*100 AS capital_return_rate,
       COALESCE(SUM(avg_trade_return_rate * closed_count)/NULLIF(SUM(closed_count),0),0) AS avg_trade_return_rate,
       COALESCE(SUM(avg_holding_seconds * closed_count)/NULLIF(SUM(closed_count),0),0) AS avg_holding_seconds,
       SUM(signal_exit_profit) AS signal_exit_profit, SUM(session_close_profit) AS session_close_profit
FROM research_performance_daily
WHERE run_id = :run_id AND trading_date BETWEEN :start_date AND :end_date
GROUP BY trade_stock_code, signal_source_stock_code, strategy_code, observation_code, direction
ORDER BY capital_return_rate DESC, realized_profit DESC
LIMIT 10;
