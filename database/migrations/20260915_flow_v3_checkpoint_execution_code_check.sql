-- FLOW allocation execution products only; no row, capital, operation or SEND changes.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM flow_v3_live_checkpoint_allocation
               WHERE stock_code NOT IN ('000660','005930','0193T0','0193W0','0197X0','0193L0')) THEN
        RAISE EXCEPTION 'FLOW_CHECKPOINT_EXECUTION_CODE_OUTSIDE_CONTRACT';
    END IF;
END $$;
ALTER TABLE flow_v3_live_checkpoint_allocation
    DROP CONSTRAINT IF EXISTS flow_v3_live_checkpoint_allocation_stock_code_check;
ALTER TABLE flow_v3_live_checkpoint_allocation
    ADD CONSTRAINT flow_v3_live_checkpoint_allocation_stock_code_check
    CHECK (stock_code IN ('000660','005930','0193T0','0193W0','0197X0','0193L0'));
COMMIT;
