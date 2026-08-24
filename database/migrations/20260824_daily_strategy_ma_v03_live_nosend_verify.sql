-- READ ONLY verification for the DRAFT LIVE NO_SEND additive migration.
SELECT array_agg(required.column_name ORDER BY required.column_name)
           FILTER (WHERE existing.column_name IS NULL) AS missing_live_trade_v03_columns
  FROM (VALUES
        ('live_trade_key'), ('ownership_id'), ('lifecycle_status'),
        ('entry_intent_key'), ('exit_intent_key')
       ) AS required(column_name)
  LEFT JOIN information_schema.columns existing
    ON existing.table_schema='public'
   AND existing.table_name='daily_strategy_live_trade'
   AND existing.column_name=required.column_name;

SELECT count(*) AS legacy_trade_status_invalid
  FROM daily_strategy_live_trade
 WHERE trade_status NOT IN ('OPEN','CLOSED','CANCELLED');

-- Prepared NO_SEND intent/request rows must not create an OPEN LIVE trade or
-- therefore contribute to vw_daily_strategy_live_exposure.
SELECT count(*) AS no_send_intents_with_live_trade
  FROM daily_strategy_live_order_intent
 WHERE lifecycle_status='NO_SEND_VALIDATED'
   AND live_trade_id IS NOT NULL;

SELECT count(*) AS planned_or_pending_open_live_trades
  FROM daily_strategy_live_trade
 WHERE trade_status='OPEN'
   AND lifecycle_status IN ('PLANNED','ENTRY_PENDING');

SELECT count(*) AS exposure_rows_without_actual_open_trade
  FROM vw_daily_strategy_live_exposure e
 WHERE e.open_live_trade_count <= 0;

SELECT count(*) AS duplicate_live_trade_keys FROM (
    SELECT live_trade_key FROM daily_strategy_live_trade
     WHERE live_trade_key IS NOT NULL GROUP BY live_trade_key HAVING count(*) > 1
) x;

SELECT count(*) AS duplicate_ownership_ids FROM (
    SELECT ownership_id FROM daily_strategy_live_trade
     WHERE ownership_id IS NOT NULL GROUP BY ownership_id HAVING count(*) > 1
) x;

SELECT count(*) AS no_send_with_broker_mapping
  FROM daily_strategy_live_order_request r
  JOIN daily_strategy_live_order_intent i USING(intent_id)
  JOIN daily_strategy_live_broker_order_mapping m USING(order_request_id)
 WHERE r.request_status='NO_SEND_VALIDATED' OR i.lifecycle_status='NO_SEND_VALIDATED';

SELECT count(*) AS broker_mapping_without_shared_order
  FROM daily_strategy_live_broker_order_mapping m
  LEFT JOIN live_broker_order b ON b.broker_order_id=m.broker_order_id
 WHERE b.broker_order_id IS NULL;

SELECT count(*) AS reservation_balance_violations
  FROM daily_strategy_live_capital_reservation
 WHERE consumed_amount + released_amount > reserved_amount;

SELECT count(*) AS reservation_remaining_amount_violations
  FROM daily_strategy_live_capital_reservation
 WHERE remaining_reserved_amount <> reserved_amount - consumed_amount - released_amount
    OR remaining_reserved_amount < 0;

SELECT count(*) AS duplicate_intent_keys FROM (
    SELECT intent_key FROM daily_strategy_live_order_intent GROUP BY intent_key HAVING count(*) > 1
) x;

SELECT attr1 AS global_trade_yn
  FROM common_code WHERE group_cd='SYSTEM_SWITCH' AND code='GLOBAL_TRADE_YN';
