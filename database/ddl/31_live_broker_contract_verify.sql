-- READ ONLY verification for DDL 31 shared broker ledger.
-- The zero-row checks are TEST preflight evidence only, not a future migration
-- requirement. Existing broker history is never rewritten by Daily MA V0.3.
SELECT table_name
  FROM information_schema.tables
 WHERE table_schema='public'
   AND table_name IN ('live_broker_order','live_broker_fill','live_broker_order_audit')
 ORDER BY table_name;

SELECT table_name, column_name, data_type, is_nullable
  FROM information_schema.columns
 WHERE table_schema='public'
   AND table_name IN ('live_broker_order','live_broker_fill')
 ORDER BY table_name, ordinal_position;

SELECT count(*) AS live_broker_order_count FROM live_broker_order;
SELECT count(*) AS live_broker_fill_count FROM live_broker_fill;
SELECT count(*) AS live_broker_order_audit_count FROM live_broker_order_audit;

SELECT attr1 AS global_trade_yn
  FROM common_code WHERE group_cd='SYSTEM_SWITCH' AND code='GLOBAL_TRADE_YN';
