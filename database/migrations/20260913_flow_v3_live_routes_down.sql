-- Guarded rollback ONLY before any USER_APPROVED operation has been created.
-- Never delete new route history to force a rollback. Otherwise use forward repair.
BEGIN;
SET LOCAL lock_timeout='5s';
DO $$ DECLARE t text; r record; BEGIN
 IF EXISTS(SELECT 1 FROM flow_v3_live_capital WHERE initialization_basis<>'LEGACY_CLOSE_1_5')
 OR EXISTS(SELECT strategy_id FROM flow_v3_live_capital GROUP BY strategy_id HAVING count(*)>1)
 OR EXISTS(SELECT strategy_id FROM flow_v3_strategy_operation WHERE effective_to IS NULL GROUP BY strategy_id HAVING count(*)>1)
 THEN RAISE EXCEPTION 'ROLLBACK_UNSAFE_NEW_ROUTE_HISTORY_EXISTS'; END IF;
 IF EXISTS(SELECT 1 FROM flow_v3_strategy_operation WHERE live_approved
           AND entry_enabled IS DISTINCT FROM (effective_to IS NULL)) THEN
  RAISE EXCEPTION 'ROLLBACK_UNSAFE_ENTRY_STATE_CHANGED';
 END IF;
 IF EXISTS(SELECT 1 FROM flow_v3_strategy_operation o JOIN flow_v3_live_capital c USING(operation_id)
           WHERE o.entry_resume_at IS DISTINCT FROM c.activated_at) THEN
  RAISE EXCEPTION 'ROLLBACK_UNSAFE_RESUME_BOUNDARY_CHANGED';
 END IF;
 -- Drop only composite ownership FKs introduced by the forward migration.
 FOR r IN SELECT conrelid::regclass AS tbl,conname FROM pg_constraint
  WHERE contype='f' AND array_length(conkey,1)=2 AND conrelid IN
    ('flow_v3_live_capital'::regclass,'flow_v3_live_preparation'::regclass,
     'flow_v3_live_preparation_intent'::regclass,'flow_v3_live_intent'::regclass,
     'flow_v3_live_lot'::regclass,'flow_v3_live_settlement'::regclass,'flow_v3_live_trade'::regclass)
 LOOP EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',r.tbl,r.conname); END LOOP;
 ALTER TABLE flow_v3_strategy_operation DROP CONSTRAINT flow_v3_operation_owner_unique;
 ALTER TABLE flow_v3_live_capital DROP CONSTRAINT flow_v3_capital_owner_unique;
 ALTER TABLE flow_v3_live_intent DROP CONSTRAINT flow_v3_intent_owner_unique;
 ALTER TABLE flow_v3_live_trade DROP CONSTRAINT flow_v3_trade_owner_unique;
 ALTER TABLE flow_v3_live_lot DROP CONSTRAINT flow_v3_lot_owner_unique;
 ALTER TABLE flow_v3_live_trade ALTER COLUMN operation_id DROP NOT NULL;
 DROP INDEX ux_flow_v3_operation_route_current;
 DROP INDEX ux_flow_v3_strategy_operation_current;
 CREATE UNIQUE INDEX ux_flow_v3_strategy_operation_current ON flow_v3_strategy_operation(strategy_id) WHERE effective_to IS NULL;
 DROP INDEX ux_flow_v3_live_entry_identity;
 CREATE UNIQUE INDEX ux_flow_v3_live_entry_identity ON flow_v3_live_intent(strategy_id,entry_event_key) WHERE side='BUY';
 FOREACH t IN ARRAY ARRAY['flow_v3_live_preparation_intent','flow_v3_live_intent','flow_v3_live_lot','flow_v3_live_settlement'] LOOP
  EXECUTE format('ALTER TABLE %I DROP COLUMN operation_id',t);
 END LOOP;
 ALTER TABLE flow_v3_live_preparation DROP CONSTRAINT flow_v3_live_preparation_pkey;
 ALTER TABLE flow_v3_live_preparation DROP COLUMN operation_id;
 ALTER TABLE flow_v3_live_preparation ADD PRIMARY KEY(strategy_id);
 ALTER TABLE flow_v3_live_capital DROP CONSTRAINT flow_v3_live_capital_pkey;
 ALTER TABLE flow_v3_live_capital ADD PRIMARY KEY(strategy_id);
 ALTER TABLE flow_v3_live_capital ADD FOREIGN KEY(strategy_id) REFERENCES flow_v3_live_preparation;
 ALTER TABLE flow_v3_live_preparation_intent ADD FOREIGN KEY(strategy_id) REFERENCES flow_v3_live_preparation;
 ALTER TABLE flow_v3_live_preparation_intent ADD UNIQUE(strategy_id,entry_event_key);
 ALTER TABLE flow_v3_live_preparation_intent ADD UNIQUE(event_id);
 FOREACH t IN ARRAY ARRAY['flow_v3_live_intent','flow_v3_live_lot','flow_v3_live_settlement'] LOOP
  EXECUTE format('ALTER TABLE %I ADD FOREIGN KEY(strategy_id) REFERENCES flow_v3_live_capital(strategy_id)',t);
 END LOOP;
 ALTER TABLE flow_v3_live_lot ADD UNIQUE(strategy_id,entry_event_key);
 ALTER TABLE flow_v3_strategy_operation DROP COLUMN execution_route;
 ALTER TABLE flow_v3_strategy_operation DROP COLUMN live_execution_code;
 ALTER TABLE flow_v3_strategy_operation DROP COLUMN entry_enabled;
 ALTER TABLE flow_v3_strategy_operation DROP COLUMN live_approved;
 ALTER TABLE flow_v3_strategy_operation DROP COLUMN entry_resume_at;
 ALTER TABLE flow_v3_strategy_operation DROP COLUMN approval_reference;
 ALTER TABLE flow_v3_live_capital DROP COLUMN initialization_basis;
 ALTER TABLE flow_v3_live_capital ADD CONSTRAINT flow_v3_live_capital_check CHECK(initial_capital=initial_close*1.5);
 ALTER TABLE flow_v3_live_capital ALTER COLUMN initial_close SET NOT NULL;
 ALTER TABLE flow_v3_live_capital ALTER COLUMN initial_price_date SET NOT NULL;
 ALTER TABLE flow_v3_live_preparation DROP CONSTRAINT flow_v3_preparation_amount_contract;
 ALTER TABLE flow_v3_live_preparation DROP CONSTRAINT flow_v3_preparation_stock_contract;
 ALTER TABLE flow_v3_live_preparation DROP CONSTRAINT flow_v3_preparation_product_contract;
 ALTER TABLE flow_v3_live_preparation ALTER COLUMN initial_price_date SET NOT NULL;
 ALTER TABLE flow_v3_live_preparation ALTER COLUMN initial_close SET NOT NULL;
 ALTER TABLE flow_v3_live_preparation ALTER COLUMN reference_price SET NOT NULL;
 ALTER TABLE flow_v3_live_preparation ALTER COLUMN next_quantity SET NOT NULL;
 ALTER TABLE flow_v3_live_preparation ADD CONSTRAINT flow_v3_live_preparation_check CHECK(initial_capital=initial_close*1.5);
 ALTER TABLE flow_v3_live_preparation ADD CONSTRAINT flow_v3_live_preparation_stock_code_check CHECK(stock_code='000660');
 ALTER TABLE flow_v3_live_preparation ADD CONSTRAINT flow_v3_live_preparation_check2 CHECK((direction='LONG' AND execution_code='0193T0') OR (direction='SHORT' AND execution_code='0197X0'));
 ALTER TABLE flow_v3_live_preparation ADD CONSTRAINT flow_v3_live_preparation_strategy_id_check CHECK(strategy_id IN
 ('FV3008243','FV3008241','FV3009185','FV3009201','FV3009187','FV3009203','FV3008227','FV3008225',
  'FV3008211','FV3008209','FV3008084','FV3005688','FV3005672','FV3004728','FV3004712'));
 ALTER TABLE flow_v3_live_intent DROP CONSTRAINT flow_v3_intent_product_contract;
 ALTER TABLE flow_v3_live_intent ADD CONSTRAINT flow_v3_live_intent_execution_code_check CHECK(execution_code IN ('0193T0','0197X0'));
END $$;
COMMIT;
