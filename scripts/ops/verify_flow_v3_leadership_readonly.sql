BEGIN READ ONLY;
SET LOCAL statement_timeout='15s';
SELECT stock_code,direction,exit_policy_code,count(*)
FROM flow_v3_strategy_master WHERE is_enabled='Y' AND direction='LONG'
 AND stock_code IN ('000660','005930') GROUP BY 1,2,3 ORDER BY 1,2,3;
SELECT c.code,c.attr1 AS capital_krw,c.attr2 AS default_yn,c.sort_order,c.use_yn,g.use_yn AS group_use_yn
FROM common_code c JOIN common_code_group g USING(group_cd)
WHERE c.group_cd='FLOW_LEADERSHIP_CAPITAL' ORDER BY c.sort_order;
SELECT snapshot_date,research_start,universe_count,capital_count,row_count,result_hash
FROM flow_v3_leadership_run ORDER BY snapshot_date DESC LIMIT 31;
SELECT r.snapshot_date,r.row_count,count(s.strategy_id) AS actual_rows,
 count(s.strategy_id)=r.row_count AS complete
FROM flow_v3_leadership_run r LEFT JOIN flow_v3_leadership_snapshot s USING(snapshot_date)
GROUP BY r.snapshot_date,r.row_count ORDER BY r.snapshot_date DESC LIMIT 31;
SELECT snapshot_date,regular_daily->>'status' AS regular_status,
 extended_full_daily->>'status' AS extended_status,count(*)
FROM flow_v3_leadership_snapshot
WHERE snapshot_date=(SELECT max(snapshot_date) FROM flow_v3_leadership_run)
GROUP BY 1,2,3;
COMMIT;
