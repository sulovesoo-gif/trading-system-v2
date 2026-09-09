-- Original PAPER lifecycle/prices and RAW remain untouched.
BEGIN;
SET LOCAL lock_timeout='5s';
CREATE TABLE IF NOT EXISTS flow_v3_paper_contract_correction (
 paper_trade_id bigint NOT NULL REFERENCES flow_v3_paper_trade(paper_trade_id),
 audit_version text NOT NULL,
 strategy_id varchar(20) NOT NULL REFERENCES flow_v3_strategy_master(strategy_id),
 trade_date date NOT NULL,
 entry_signal_time timestamp NOT NULL,
 entry_execution_time timestamp NOT NULL,
 original_exit_time timestamp,
 original_exit_price numeric,
 original_row jsonb NOT NULL,
 corrected_exit_time timestamp,
 corrected_exit_price numeric,
 exclusion_reason text,
 corrected_contract text NOT NULL CHECK(corrected_contract='15:18_SIGNAL / 15:19_EXECUTION'),
 excluded_from_corrected_performance boolean NOT NULL,
 excluded_from_corrected_compound boolean NOT NULL,
 audited_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(paper_trade_id,audit_version),
 CHECK(excluded_from_corrected_performance=excluded_from_corrected_compound),
 CHECK((excluded_from_corrected_performance AND exclusion_reason='ENTRY_AFTER_EOD_CUTOFF'
        AND entry_signal_time::time>TIME '15:18' AND corrected_exit_time IS NULL)
    OR (NOT excluded_from_corrected_performance AND exclusion_reason IS NULL
        AND corrected_exit_time>=entry_execution_time AND corrected_exit_price>0
        AND corrected_exit_time::date=trade_date AND corrected_exit_time::time<=TIME '15:19'))
);
CREATE INDEX IF NOT EXISTS ix_flow_v3_contract_correction_strategy
 ON flow_v3_paper_contract_correction(strategy_id,audit_version);
CREATE TABLE IF NOT EXISTS flow_v3_paper_correction_baseline (
 audit_version text NOT NULL,
 strategy_id varchar(20) NOT NULL REFERENCES flow_v3_strategy_master(strategy_id),
 capital_before jsonb NOT NULL,
 daily_before jsonb NOT NULL,
 audited_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(audit_version,strategy_id)
);
COMMIT;
