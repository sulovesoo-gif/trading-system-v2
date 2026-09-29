BEGIN TRANSACTION READ ONLY;

SELECT filter_code,count(*) AS variants
FROM minute_ma_real_variant WHERE enabled GROUP BY filter_code ORDER BY filter_code;

SELECT 'long_strategy_count' AS check_name,count(*) AS actual,1200 AS expected
FROM minute_ma_strategy_master WHERE is_enabled='Y' AND direction='LONG'
UNION ALL
SELECT 'variant_count',count(*),4800 FROM minute_ma_real_variant WHERE enabled
UNION ALL
SELECT 'duplicate_variant',count(*),0 FROM (
  SELECT minute_strategy_id,filter_code,paper_epoch
  FROM minute_ma_real_variant GROUP BY 1,2,3 HAVING count(*)>1
) duplicate
UNION ALL
SELECT 'entry_outside_1450_1518',count(*),0 FROM minute_ma_real_paper_trade
WHERE NOT (entry_signal_time::time>=TIME '14:50' AND entry_signal_time::time<TIME '15:19');

SELECT filter_code,count(*) AS trades
FROM minute_ma_real_paper_trade GROUP BY filter_code ORDER BY filter_code;

SELECT filter_code,count(*) AS snapshots
FROM minute_ma_real_daily_snapshot WHERE snapshot_date=DATE '2026-09-23'
GROUP BY filter_code ORDER BY filter_code;

SELECT route_code,sizing_mode,execution_stock_code,count(*) AS routes,
       sum(allocated_amount) AS allocated_amount,sum(fixed_quantity) AS fixed_quantity
FROM minute_ma_real_live_route WHERE effective_to IS NULL
GROUP BY route_code,sizing_mode,execution_stock_code ORDER BY 1,2,3;

SELECT v.strategy_id,v.filter_code,r.route_code,r.execution_stock_code,r.sizing_mode,
       r.allocated_amount,r.fixed_quantity,c.initial_capital,c.current_capital
FROM minute_ma_real_live_route r JOIN minute_ma_real_variant v USING(real_variant_id)
LEFT JOIN minute_ma_real_live_capital c ON c.real_live_route_id=r.real_live_route_id
 AND c.capital_epoch_no=r.capital_epoch_no
WHERE r.effective_to IS NULL AND r.route_code='UNDERLYING'
ORDER BY v.strategy_id,v.filter_code;

SELECT 'bad_mapping' AS check_name,count(*) AS violation_count
FROM minute_ma_real_live_route r JOIN minute_ma_real_variant v USING(real_variant_id)
WHERE r.effective_to IS NULL AND r.route_code='LEVERAGE'
  AND r.execution_stock_code<>CASE v.signal_code WHEN '000660' THEN '0193T0'
                                              WHEN '005930' THEN '0193W0' END
UNION ALL
SELECT 'bad_top50_sizing',count(*) FROM minute_ma_real_live_route
WHERE effective_to IS NULL AND route_code='LEVERAGE'
  AND (sizing_mode<>'FIXED_QTY' OR fixed_quantity<>1 OR allocated_amount<>0)
UNION ALL
SELECT 'duplicate_active_route',count(*) FROM (
  SELECT real_variant_id,route_code FROM minute_ma_real_live_route WHERE effective_to IS NULL
  GROUP BY 1,2 HAVING count(*)>1) x;

ROLLBACK;
