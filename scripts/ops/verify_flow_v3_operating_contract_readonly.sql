-- READ ONLY. Run before and after deployment; compare snapshots while workers
-- are quiescent. Natural runtime writes otherwise legitimately change totals.
BEGIN READ ONLY;
SET LOCAL statement_timeout='30s';
SELECT operation_id,strategy_id,execution_route,live_execution_code,
       allocated_amount,entry_enabled,effective_from,effective_to,entry_resume_at
FROM flow_v3_strategy_operation WHERE operation_status='LIVE' ORDER BY operation_id;
SELECT operation_id,strategy_id,initial_capital,current_capital,realized_net,
       current_capital=initial_capital+realized_net AS capital_invariant
FROM flow_v3_live_capital ORDER BY operation_id;
SELECT i.strategy_id,i.operation_id,i.execution_code,o.status,count(*) AS orders,
       sum(o.post_attempt_count) AS post_attempts,count(o.broker_order_number) AS broker_numbers
FROM flow_v3_live_intent i JOIN flow_v3_live_order o USING(intent_id)
GROUP BY 1,2,3,4 ORDER BY 1,2,3,4;
SELECT operation_id,exposure_status,count(*) AS lots,sum(bought_quantity-sold_quantity) AS remaining
FROM flow_v3_live_lot GROUP BY 1,2 ORDER BY 1,2;
SELECT trade_status,count(*) FROM flow_v3_paper_trade GROUP BY trade_status ORDER BY trade_status;
SELECT count(*) AS invalid_capital FROM flow_v3_live_capital WHERE current_capital<>initial_capital+realized_net;
SELECT count(*) AS invalid_lot_quantity FROM flow_v3_live_lot WHERE bought_quantity<sold_quantity OR sold_quantity<0;
SELECT operation_id,entry_event_key,count(*) FROM flow_v3_live_intent WHERE side='BUY'
GROUP BY 1,2 HAVING count(*)>1;
SELECT i.intent_id FROM flow_v3_live_intent i LEFT JOIN flow_v3_strategy_operation o USING(operation_id)
WHERE o.operation_id IS NULL OR i.strategy_id<>o.strategy_id;
SELECT * FROM flow_v3_send_profile;
COMMIT;
-- After migration only (new audit table):
BEGIN READ ONLY;
SELECT capital_change_id,operation_id,replacement_operation_id,strategy_id,execution_route,
       previous_amount,approved_amount,current_capital_before,realized_net_before,
       changed_at,approval_reference,change_reason
FROM flow_v3_live_capital_change ORDER BY capital_change_id;
COMMIT;
