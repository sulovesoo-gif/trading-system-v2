-- Additive PAPER projections: existing research-return summaries remain intact.
BEGIN;
SET LOCAL lock_timeout='5s';
CREATE TABLE IF NOT EXISTS flow_v3_paper_accounting_queue (
 strategy_id varchar(20) PRIMARY KEY REFERENCES flow_v3_strategy_master(strategy_id),
 generation bigint NOT NULL DEFAULT 1,
 available_at timestamptz NOT NULL DEFAULT now(),
 last_error text,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS flow_v3_paper_accounting_lot (
 paper_trade_id bigint PRIMARY KEY REFERENCES flow_v3_paper_trade(paper_trade_id),
 strategy_id varchar(20) NOT NULL REFERENCES flow_v3_strategy_master(strategy_id),
 quantity bigint NOT NULL CHECK(quantity>=0),
 calculation_version text NOT NULL,
 metrics jsonb NOT NULL,
 calculated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_flow_v3_accounting_lot_strategy
 ON flow_v3_paper_accounting_lot(strategy_id);
CREATE TABLE IF NOT EXISTS flow_v3_paper_accounting_capital (
 strategy_id varchar(20) PRIMARY KEY REFERENCES flow_v3_strategy_master(strategy_id),
 execution_code varchar(20) NOT NULL,
 initial_price_date date,
 initial_price numeric,
 source_generation bigint NOT NULL,
 calculation_version text NOT NULL,
 metrics jsonb NOT NULL,
 calculated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS flow_v3_paper_accounting_daily (
 strategy_id varchar(20) NOT NULL REFERENCES flow_v3_strategy_master(strategy_id),
 business_date date NOT NULL,
 metrics jsonb NOT NULL,
 calculated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(strategy_id,business_date)
);
CREATE OR REPLACE FUNCTION flow_v3_queue_paper_accounting() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO flow_v3_paper_accounting_queue(strategy_id) VALUES(NEW.strategy_id)
 ON CONFLICT(strategy_id) DO UPDATE SET
 generation=flow_v3_paper_accounting_queue.generation+1,available_at=now(),
 last_error=NULL,updated_at=now();
 RETURN NEW;
END $$;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='flow_v3_paper_trade'::regclass
   AND tgname='flow_v3_paper_accounting_dirty') THEN
 CREATE TRIGGER flow_v3_paper_accounting_dirty
 AFTER INSERT OR UPDATE OF trade_status,entry_execution_time,entry_execution_price,
 actual_exit_time,actual_exit_price ON flow_v3_paper_trade
 FOR EACH ROW EXECUTE FUNCTION flow_v3_queue_paper_accounting();
 END IF;
END $$;
-- Initial bootstrap is bounded to one queue record per strategy. Reapplying
-- this migration does not resettle any trade or reset any capital.
INSERT INTO flow_v3_paper_accounting_queue(strategy_id)
SELECT strategy_id FROM flow_v3_strategy_master m
WHERE NOT EXISTS(SELECT 1 FROM flow_v3_paper_accounting_capital c WHERE c.strategy_id=m.strategy_id)
ON CONFLICT DO NOTHING;
COMMENT ON TABLE flow_v3_paper_accounting_lot IS
 'PAPER independent variable-share simulation, never LIVE broker settlement';
COMMIT;
