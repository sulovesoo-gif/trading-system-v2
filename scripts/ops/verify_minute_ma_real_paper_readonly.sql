BEGIN TRANSACTION READ ONLY;

SELECT 'long_strategy_count' AS check_name,count(*)::text AS actual,'1200' AS expected
FROM minute_ma_strategy_master WHERE is_enabled='Y' AND direction='LONG'
UNION ALL
SELECT 'variant_count',count(*)::text,'3600' FROM minute_ma_real_variant WHERE enabled
UNION ALL
SELECT 'capital_epoch_count',count(*)::text,'3600' FROM minute_ma_real_capital_epoch WHERE ended_at IS NULL
UNION ALL
SELECT 'entry_outside_1500_1518',count(*)::text,'0' FROM minute_ma_real_paper_trade
 WHERE entry_signal_time::time NOT BETWEEN TIME '15:00' AND TIME '15:18'
UNION ALL
SELECT 'non_normal_exit',count(*)::text,'0' FROM minute_ma_real_paper_trade
 WHERE exit_reason IS NOT NULL AND exit_reason<>'NORMAL_EXIT'
UNION ALL
SELECT 'null_real_pass',count(*)::text,'0' FROM minute_ma_real_paper_trade
 WHERE NOT real_is_complete OR velocity_value IS NULL
UNION ALL
SELECT 'duplicate_trade',count(*)::text,'0' FROM (
 SELECT real_variant_id,entry_signal_key FROM minute_ma_real_paper_trade
 GROUP BY 1,2 HAVING count(*)>1) x
UNION ALL
SELECT 'capital_epoch_orphan',count(*)::text,'0' FROM minute_ma_real_capital_epoch c
 LEFT JOIN minute_ma_real_variant v USING(real_variant_id) WHERE v.real_variant_id IS NULL
UNION ALL
SELECT 'stop_or_eod_exit',count(*)::text,'0' FROM minute_ma_real_paper_trade
 WHERE COALESCE(exit_reason,'') IN ('STOP_EXIT','EOD_1519');

SELECT filter_code,count(*) AS variants,
       sum(c.current_realized_capital) AS current_realized_capital
FROM minute_ma_real_variant v JOIN minute_ma_real_capital_epoch c USING(real_variant_id)
WHERE v.enabled AND c.ended_at IS NULL GROUP BY filter_code ORDER BY filter_code;

SELECT filter_code,lifecycle_status,count(*) AS trades
FROM minute_ma_real_paper_trade GROUP BY 1,2 ORDER BY 1,2;

ROLLBACK;
