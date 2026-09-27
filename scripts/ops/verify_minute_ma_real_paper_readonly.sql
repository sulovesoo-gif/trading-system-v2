BEGIN TRANSACTION READ ONLY;

SELECT 'long_strategy_count' AS check_name,count(*)::text AS actual,'1200' AS expected
FROM minute_ma_strategy_master WHERE is_enabled='Y' AND direction='LONG'
UNION ALL
SELECT 'variant_count',count(*)::text,'3600' FROM minute_ma_real_variant WHERE enabled
UNION ALL
SELECT 'entry_outside_1500_1518',count(*)::text,'0' FROM minute_ma_real_paper_trade
 WHERE entry_signal_time::time NOT BETWEEN TIME '15:00' AND TIME '15:18'
UNION ALL
SELECT 'non_normal_exit',count(*)::text,'0' FROM minute_ma_real_paper_trade
 WHERE exit_reason IS NOT NULL AND exit_reason<>'NORMAL_EXIT'
UNION ALL
SELECT 'null_real_pass',count(*)::text,'0' FROM minute_ma_real_paper_trade
 WHERE lifecycle_status IN ('OPEN','CLOSED') AND (NOT real_is_complete OR velocity_value IS NULL)
UNION ALL
SELECT 'duplicate_trade',count(*)::text,'0' FROM (
 SELECT real_variant_id,entry_signal_key FROM minute_ma_real_paper_trade
 GROUP BY 1,2 HAVING count(*)>1) x
UNION ALL
SELECT 'slot_count_mismatch',count(*)::text,'0' FROM (
 SELECT v.real_variant_id FROM minute_ma_real_variant v LEFT JOIN minute_ma_real_paper_slot s
 USING(real_variant_id) GROUP BY v.real_variant_id,v.k_mode HAVING count(s.slot_no)<>v.k_mode) x
UNION ALL
SELECT 'slot_trade_orphan',count(*)::text,'0' FROM minute_ma_real_paper_slot s
 LEFT JOIN minute_ma_real_paper_trade t ON t.real_paper_trade_id=s.current_trade_id
 WHERE s.current_trade_id IS NOT NULL AND t.real_paper_trade_id IS NULL;

SELECT filter_code,count(*) variants,min(k_mode) min_k,max(k_mode) max_k,
       sum(current_capital) current_capital
FROM minute_ma_real_variant v JOIN minute_ma_real_paper_slot s USING(real_variant_id)
WHERE v.enabled GROUP BY filter_code ORDER BY filter_code;

ROLLBACK;
