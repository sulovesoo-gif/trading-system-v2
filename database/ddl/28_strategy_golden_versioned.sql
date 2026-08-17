-- Versioned, immutable Golden artifacts.  Existing strategy_golden_final is
-- intentionally preserved as the v1.0.0 provenance source and is not altered.

CREATE TABLE IF NOT EXISTS strategy_golden_artifact (
    golden_version              VARCHAR(20) PRIMARY KEY,
    created_at                  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    raw_period_start            DATE NOT NULL,
    raw_period_end              DATE NOT NULL,
    raw_cutoff_timestamp        TIMESTAMP NOT NULL,
    signal_source_venue         VARCHAR(20) NOT NULL,
    historical_execution_rule   VARCHAR(100) NOT NULL,
    provenance_status           VARCHAR(60) NOT NULL,
    metadata                    JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_golden_row (
    golden_version              VARCHAR(20) NOT NULL REFERENCES strategy_golden_artifact(golden_version),
    strategy_instance           VARCHAR(100) NOT NULL,
    strategy_code               VARCHAR(100) NOT NULL,
    strategy_version            VARCHAR(20) NOT NULL,
    trade_date                  DATE NOT NULL,
    signal_stock_code           VARCHAR(20) NOT NULL,
    signal_direction            VARCHAR(10) NOT NULL,
    execution_stock_code        VARCHAR(20) NOT NULL,
    execution_direction         VARCHAR(10) NOT NULL,
    signal_time                 TIMESTAMP NOT NULL,
    entry_target_time           TIMESTAMP NOT NULL,
    entry_execution_time        TIMESTAMP NOT NULL,
    exit_trigger_time           TIMESTAMP NOT NULL,
    exit_execution_time         TIMESTAMP NOT NULL,
    raw_entry_price             NUMERIC(18,2) NOT NULL,
    raw_exit_price              NUMERIC(18,2) NOT NULL,
    exit_reason                 VARCHAR(60) NOT NULL,
    shared_entry_group          VARCHAR(100),
    reference_detail            JSONB NOT NULL,
    source_definition_version   VARCHAR(40) NOT NULL,
    PRIMARY KEY (golden_version, strategy_instance, trade_date, signal_time)
);

CREATE INDEX IF NOT EXISTS idx_strategy_golden_row_version_instance
    ON strategy_golden_row (golden_version, strategy_instance, signal_time);
