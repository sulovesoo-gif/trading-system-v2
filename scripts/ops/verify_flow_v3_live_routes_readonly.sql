BEGIN READ ONLY;
SET LOCAL statement_timeout='30s';
-- Save this fingerprint section before AND after migration. New ownership
-- metadata is excluded; existing row content including lifecycle_key is compared.
SELECT 'capital' AS object,count(*) AS rows,md5(string_agg((to_jsonb(t)-'initialization_basis')::text,'|' ORDER BY operation_id)) AS fingerprint FROM flow_v3_live_capital t
UNION ALL SELECT 'intent',count(*),md5(string_agg((to_jsonb(t)-'operation_id')::text,'|' ORDER BY intent_id)) FROM flow_v3_live_intent t
UNION ALL SELECT 'orders',count(*),md5(string_agg(to_jsonb(t)::text,'|' ORDER BY broker_order_id)) FROM flow_v3_live_order t
UNION ALL SELECT 'lots',count(*),md5(string_agg((to_jsonb(t)-'operation_id')::text,'|' ORDER BY live_trade_id)) FROM flow_v3_live_lot t
UNION ALL SELECT 'settlement',count(*),md5(string_agg((to_jsonb(t)-'operation_id')::text,'|' ORDER BY live_trade_id)) FROM flow_v3_live_settlement t
UNION ALL SELECT 'trade',count(*),md5(string_agg(to_jsonb(t)::text,'|' ORDER BY live_trade_id)) FROM flow_v3_live_trade t
UNION ALL SELECT 'fill',count(*),md5(string_agg(to_jsonb(t)::text,'|' ORDER BY broker_order_id,checkpoint_version)) FROM flow_v3_live_fill_checkpoint t
UNION ALL SELECT 'allocation',count(*),md5(string_agg(to_jsonb(t)::text,'|' ORDER BY broker_order_id,checkpoint_version)) FROM flow_v3_live_checkpoint_allocation t
UNION ALL SELECT 'preparation',count(*),md5(string_agg((to_jsonb(t)-'operation_id')::text,'|' ORDER BY strategy_id)) FROM flow_v3_live_preparation t
UNION ALL SELECT 'operation',count(*),md5(string_agg((to_jsonb(t)-ARRAY['execution_route','live_execution_code','live_approved','entry_enabled','entry_resume_at','approval_reference'])::text,'|' ORDER BY operation_id)) FROM flow_v3_strategy_operation t;
SELECT count(*) AS strategy_count FROM flow_v3_strategy_master;
SELECT count(*) AS paper_count FROM flow_v3_paper_trade;
SELECT i.strategy_id,i.execution_code,o.status,count(*) AS rows,
 sum(o.post_attempt_count) AS posts,count(o.broker_order_number) AS broker_numbers
 FROM flow_v3_live_intent i JOIN flow_v3_live_order o USING(intent_id)
 WHERE i.strategy_id IN ('FV3008084','FV3008209') GROUP BY 1,2,3;
COMMIT;

-- POST-migration only. Each violation query must return 0 / no rows.
BEGIN READ ONLY;
SELECT count(*) AS capital_invariant_violations FROM flow_v3_live_capital
 WHERE current_capital<>initial_capital+realized_net;
SELECT count(*) AS intent_owner_mismatch FROM flow_v3_live_intent i
 LEFT JOIN flow_v3_live_capital c ON c.operation_id=i.operation_id AND c.strategy_id=i.strategy_id
 WHERE c.operation_id IS NULL;
SELECT count(*) AS lot_owner_mismatch FROM flow_v3_live_lot l
 JOIN flow_v3_live_intent i ON i.intent_id=l.entry_intent_id JOIN flow_v3_live_trade t ON t.live_trade_id=l.live_trade_id
 WHERE l.operation_id<>i.operation_id OR l.operation_id<>t.operation_id;
SELECT count(*) AS fill_owner_mismatch FROM flow_v3_live_fill_checkpoint f
 JOIN flow_v3_live_order o USING(broker_order_id) JOIN flow_v3_live_intent i USING(intent_id)
 JOIN flow_v3_live_trade t ON t.live_trade_id=f.live_trade_id
 WHERE i.operation_id<>t.operation_id OR i.live_trade_id IS DISTINCT FROM t.live_trade_id;
SELECT count(*) AS settlement_owner_mismatch FROM flow_v3_live_settlement s
 JOIN flow_v3_live_lot l USING(live_trade_id) WHERE s.operation_id<>l.operation_id;
SELECT count(*) AS lot_quantity_violation FROM flow_v3_live_lot WHERE bought_quantity<sold_quantity OR sold_quantity<0;
SELECT operation_id,entry_event_key,count(*) FROM flow_v3_live_intent WHERE side='BUY'
 GROUP BY 1,2 HAVING count(*)>1;
SELECT strategy_id,execution_route,count(*) FROM flow_v3_strategy_operation WHERE effective_to IS NULL AND execution_route IS NOT NULL
 GROUP BY 1,2 HAVING count(*)>1;
SELECT o.operation_id,o.strategy_id,o.execution_route,o.live_execution_code,o.entry_enabled,o.effective_to,
 c.initial_capital,c.current_capital,c.realized_net FROM flow_v3_strategy_operation o JOIN flow_v3_live_capital c USING(operation_id)
 ORDER BY strategy_id,operation_id;
COMMIT;
