-- FLOW LIVE ownership only. No strategy/PAPER changes, activation, terminalization or SEND arming.
-- One atomic transaction: any ambiguous legacy owner aborts the entire migration.
BEGIN;
SET LOCAL lock_timeout='5s';
DO $migration$
DECLARE t text;
BEGIN
 IF EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='flow_v3_strategy_operation'::regclass
           AND conname='flow_v3_operation_route_contract') THEN RETURN; END IF;
 ALTER TABLE flow_v3_strategy_operation ADD COLUMN execution_route text;
 ALTER TABLE flow_v3_strategy_operation ADD COLUMN live_execution_code text;
 ALTER TABLE flow_v3_strategy_operation ADD COLUMN live_approved boolean NOT NULL DEFAULT false;
 ALTER TABLE flow_v3_strategy_operation ADD COLUMN entry_enabled boolean NOT NULL DEFAULT false;
 ALTER TABLE flow_v3_strategy_operation ADD COLUMN entry_resume_at timestamp;
 ALTER TABLE flow_v3_strategy_operation ADD COLUMN approval_reference text;
 ALTER TABLE flow_v3_live_capital ADD COLUMN initialization_basis text NOT NULL DEFAULT 'LEGACY_CLOSE_1_5';
 FOREACH t IN ARRAY ARRAY['flow_v3_live_preparation','flow_v3_live_preparation_intent',
       'flow_v3_live_intent','flow_v3_live_lot','flow_v3_live_settlement'] LOOP
  EXECUTE format('ALTER TABLE %I ADD COLUMN operation_id bigint',t);
 END LOOP;
 IF EXISTS(SELECT strategy_id FROM flow_v3_live_capital GROUP BY strategy_id HAVING count(*)<>1)
 OR EXISTS(SELECT 1 FROM flow_v3_live_capital c JOIN flow_v3_strategy_operation o USING(operation_id)
           WHERE c.strategy_id<>o.strategy_id OR o.operation_status<>'LIVE') THEN
  RAISE EXCEPTION 'AMBIGUOUS_LEGACY_CAPITAL_OWNER';
 END IF;
 UPDATE flow_v3_live_preparation p SET operation_id=c.operation_id
 FROM flow_v3_live_capital c WHERE c.strategy_id=p.strategy_id;
 UPDATE flow_v3_live_preparation_intent i SET operation_id=p.operation_id
 FROM flow_v3_live_preparation p WHERE i.strategy_id=p.strategy_id;
 UPDATE flow_v3_live_intent i SET operation_id=c.operation_id
 FROM flow_v3_live_capital c WHERE i.strategy_id=c.strategy_id;
 UPDATE flow_v3_live_lot l SET operation_id=i.operation_id
 FROM flow_v3_live_intent i WHERE i.intent_id=l.entry_intent_id AND i.strategy_id=l.strategy_id;
 UPDATE flow_v3_live_settlement s SET operation_id=l.operation_id
 FROM flow_v3_live_lot l WHERE l.live_trade_id=s.live_trade_id AND l.strategy_id=s.strategy_id;
 IF EXISTS(SELECT 1 FROM flow_v3_live_trade t LEFT JOIN flow_v3_live_capital c
           ON c.operation_id=t.operation_id AND c.strategy_id=t.strategy_id WHERE c.operation_id IS NULL)
 OR EXISTS(SELECT 1 FROM flow_v3_live_lot l JOIN flow_v3_live_trade t USING(live_trade_id)
           WHERE t.operation_id<>l.operation_id) THEN
  RAISE EXCEPTION 'AMBIGUOUS_LEGACY_TRADE_OWNER';
 END IF;
 -- Retain actual legacy product, NEVER rewrite to the strategy's underlying.
 UPDATE flow_v3_strategy_operation o SET
  execution_route=CASE p.direction WHEN 'LONG' THEN 'LEVERAGE' ELSE 'INVERSE' END,
  live_execution_code=p.execution_code, live_approved=true,
  entry_enabled=(o.effective_to IS NULL),entry_resume_at=c.activated_at,
  approval_reference=p.approval_reference
 FROM flow_v3_live_capital c JOIN flow_v3_live_preparation p ON p.operation_id=c.operation_id
 WHERE o.operation_id=c.operation_id;
 IF EXISTS(SELECT 1 FROM flow_v3_live_capital c JOIN flow_v3_strategy_operation o USING(operation_id)
           JOIN flow_v3_strategy_master m ON m.strategy_id=c.strategy_id
           WHERE o.live_execution_code IS NULL OR (m.stock_code,m.direction,o.live_execution_code) NOT IN
           (('000660','LONG','0193T0'),('000660','SHORT','0197X0'),
            ('005930','LONG','0193W0'),('005930','SHORT','0193L0'))) THEN
  RAISE EXCEPTION 'LEGACY_ROUTE_MISMATCH';
 END IF;
 FOREACH t IN ARRAY ARRAY['flow_v3_live_preparation','flow_v3_live_preparation_intent',
       'flow_v3_live_intent','flow_v3_live_lot','flow_v3_live_settlement'] LOOP
  EXECUTE format('ALTER TABLE %I ALTER COLUMN operation_id SET NOT NULL',t);
 END LOOP;
 ALTER TABLE flow_v3_live_trade ALTER COLUMN operation_id SET NOT NULL;
 ALTER TABLE flow_v3_strategy_operation ADD CONSTRAINT flow_v3_operation_owner_unique UNIQUE(operation_id,strategy_id);
 ALTER TABLE flow_v3_strategy_operation ADD CONSTRAINT flow_v3_operation_route_contract CHECK(
   (execution_route IS NULL AND live_execution_code IS NULL AND NOT live_approved AND NOT entry_enabled)
   OR (execution_route IS NOT NULL AND live_execution_code IS NOT NULL AND
       ((execution_route='UNDERLYING' AND live_execution_code IN ('000660','005930'))
        OR (execution_route='LEVERAGE' AND live_execution_code IN ('0193T0','0193W0'))
        OR (execution_route='INVERSE' AND live_execution_code IN ('0197X0','0193L0')))
       AND (NOT live_approved OR (approval_reference IS NOT NULL AND length(btrim(approval_reference))>0))
       AND (NOT entry_enabled OR (live_approved AND effective_to IS NULL AND entry_resume_at IS NOT NULL))));
 DROP INDEX ux_flow_v3_strategy_operation_current;
 CREATE UNIQUE INDEX ux_flow_v3_strategy_operation_current ON flow_v3_strategy_operation(strategy_id)
   WHERE effective_to IS NULL AND execution_route IS NULL;
 CREATE UNIQUE INDEX ux_flow_v3_operation_route_current ON flow_v3_strategy_operation(strategy_id,execution_route)
   WHERE effective_to IS NULL AND execution_route IS NOT NULL;
 ALTER TABLE flow_v3_live_capital DROP CONSTRAINT flow_v3_live_capital_strategy_id_fkey;
 ALTER TABLE flow_v3_live_preparation_intent DROP CONSTRAINT flow_v3_live_preparation_intent_strategy_id_fkey;
 ALTER TABLE flow_v3_live_intent DROP CONSTRAINT flow_v3_live_intent_strategy_id_fkey;
 ALTER TABLE flow_v3_live_lot DROP CONSTRAINT flow_v3_live_lot_strategy_id_fkey;
 ALTER TABLE flow_v3_live_settlement DROP CONSTRAINT flow_v3_live_settlement_strategy_id_fkey;
 ALTER TABLE flow_v3_live_preparation DROP CONSTRAINT flow_v3_live_preparation_pkey;
 ALTER TABLE flow_v3_live_capital DROP CONSTRAINT flow_v3_live_capital_pkey;
 ALTER TABLE flow_v3_live_preparation ADD PRIMARY KEY(operation_id);
 ALTER TABLE flow_v3_live_capital ADD PRIMARY KEY(operation_id);
 ALTER TABLE flow_v3_live_preparation ADD CONSTRAINT flow_v3_preparation_owner_unique UNIQUE(operation_id,strategy_id);
 ALTER TABLE flow_v3_live_capital ADD CONSTRAINT flow_v3_capital_owner_unique UNIQUE(operation_id,strategy_id);
 ALTER TABLE flow_v3_live_preparation ADD FOREIGN KEY(operation_id,strategy_id)
   REFERENCES flow_v3_strategy_operation(operation_id,strategy_id);
 ALTER TABLE flow_v3_live_capital ADD FOREIGN KEY(operation_id,strategy_id)
   REFERENCES flow_v3_live_preparation(operation_id,strategy_id);
 ALTER TABLE flow_v3_live_preparation_intent ADD FOREIGN KEY(operation_id,strategy_id)
   REFERENCES flow_v3_live_preparation(operation_id,strategy_id);
 FOREACH t IN ARRAY ARRAY['flow_v3_live_intent','flow_v3_live_lot','flow_v3_live_settlement'] LOOP
  EXECUTE format('ALTER TABLE %I ADD FOREIGN KEY(operation_id,strategy_id) REFERENCES flow_v3_live_capital(operation_id,strategy_id)',t);
 END LOOP;
 ALTER TABLE flow_v3_live_trade ADD FOREIGN KEY(operation_id,strategy_id)
   REFERENCES flow_v3_live_capital(operation_id,strategy_id);
 ALTER TABLE flow_v3_live_intent ADD CONSTRAINT flow_v3_intent_owner_unique UNIQUE(operation_id,intent_id);
 ALTER TABLE flow_v3_live_trade ADD CONSTRAINT flow_v3_trade_owner_unique UNIQUE(operation_id,live_trade_id);
 ALTER TABLE flow_v3_live_lot ADD CONSTRAINT flow_v3_lot_owner_unique UNIQUE(operation_id,live_trade_id);
 ALTER TABLE flow_v3_live_intent ADD FOREIGN KEY(operation_id,live_trade_id)
   REFERENCES flow_v3_live_trade(operation_id,live_trade_id);
 ALTER TABLE flow_v3_live_lot ADD FOREIGN KEY(operation_id,entry_intent_id)
   REFERENCES flow_v3_live_intent(operation_id,intent_id);
 ALTER TABLE flow_v3_live_lot ADD FOREIGN KEY(operation_id,live_trade_id)
   REFERENCES flow_v3_live_trade(operation_id,live_trade_id);
 ALTER TABLE flow_v3_live_settlement ADD FOREIGN KEY(operation_id,live_trade_id)
   REFERENCES flow_v3_live_lot(operation_id,live_trade_id);
 ALTER TABLE flow_v3_live_preparation_intent DROP CONSTRAINT flow_v3_live_preparation_intent_event_id_key;
 ALTER TABLE flow_v3_live_preparation_intent DROP CONSTRAINT flow_v3_live_preparation_intent_strategy_id_entry_event_key_key;
 ALTER TABLE flow_v3_live_preparation_intent ADD UNIQUE(operation_id,entry_event_key);
 ALTER TABLE flow_v3_live_preparation_intent ADD UNIQUE(operation_id,event_id);
 ALTER TABLE flow_v3_live_lot DROP CONSTRAINT flow_v3_live_lot_strategy_id_entry_event_key_key;
 ALTER TABLE flow_v3_live_lot ADD UNIQUE(operation_id,entry_event_key);
 DROP INDEX ux_flow_v3_live_entry_identity;
 CREATE UNIQUE INDEX ux_flow_v3_live_entry_identity ON flow_v3_live_intent(operation_id,entry_event_key) WHERE side='BUY';
 ALTER TABLE flow_v3_live_capital ALTER COLUMN initial_close DROP NOT NULL;
 ALTER TABLE flow_v3_live_capital ALTER COLUMN initial_price_date DROP NOT NULL;
 ALTER TABLE flow_v3_live_capital DROP CONSTRAINT flow_v3_live_capital_check;
 ALTER TABLE flow_v3_live_capital ADD CONSTRAINT flow_v3_capital_initialization_contract CHECK(
  (initialization_basis='LEGACY_CLOSE_1_5' AND initial_close IS NOT NULL AND initial_capital=initial_close*1.5)
  OR (initialization_basis='USER_APPROVED' AND initial_capital>0 AND initial_capital<1e18));
 ALTER TABLE flow_v3_live_preparation DROP CONSTRAINT flow_v3_live_preparation_check;
 ALTER TABLE flow_v3_live_preparation DROP CONSTRAINT flow_v3_live_preparation_check2;
 ALTER TABLE flow_v3_live_preparation DROP CONSTRAINT flow_v3_live_preparation_stock_code_check;
 ALTER TABLE flow_v3_live_preparation DROP CONSTRAINT flow_v3_live_preparation_strategy_id_check;
 ALTER TABLE flow_v3_live_preparation ADD CONSTRAINT flow_v3_preparation_amount_contract CHECK(initial_capital>0 AND initial_capital<1e18);
 ALTER TABLE flow_v3_live_preparation ADD CONSTRAINT flow_v3_preparation_stock_contract CHECK(stock_code IN ('000660','005930'));
 ALTER TABLE flow_v3_live_preparation ADD CONSTRAINT flow_v3_preparation_product_contract CHECK(
  (stock_code='000660' AND direction='LONG' AND execution_code IN ('000660','0193T0')) OR
  (stock_code='000660' AND direction='SHORT' AND execution_code='0197X0') OR
  (stock_code='005930' AND direction='LONG' AND execution_code IN ('005930','0193W0')) OR
  (stock_code='005930' AND direction='SHORT' AND execution_code='0193L0'));
 ALTER TABLE flow_v3_live_preparation ALTER COLUMN initial_price_date DROP NOT NULL;
 ALTER TABLE flow_v3_live_preparation ALTER COLUMN initial_close DROP NOT NULL;
 ALTER TABLE flow_v3_live_preparation ALTER COLUMN reference_price DROP NOT NULL;
 ALTER TABLE flow_v3_live_preparation ALTER COLUMN next_quantity DROP NOT NULL;
 ALTER TABLE flow_v3_live_intent DROP CONSTRAINT flow_v3_live_intent_execution_code_check;
 ALTER TABLE flow_v3_live_intent ADD CONSTRAINT flow_v3_intent_product_contract CHECK(execution_code IN ('000660','005930','0193T0','0197X0','0193W0','0193L0'));
END $migration$;
COMMIT;
