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

-- Period top 10
SELECT start_date, end_date, trade_stock_code, signal_source_stock_code, strategy_code, observation_code, direction,
       closed_count, win_count, loss_count, flat_count,
       CASE WHEN closed_count = 0 THEN 0 ELSE win_count::numeric / closed_count * 100 END AS win_rate,
       realized_profit, invested_amount, invested_return_rate, capital_return_rate,
       avg_trade_return_rate, avg_holding_seconds, signal_exit_profit, session_close_profit
FROM research_performance_period
WHERE run_id = :run_id AND start_date = :start_date AND end_date = :end_date
ORDER BY capital_return_rate DESC, realized_profit DESC
LIMIT 10;
