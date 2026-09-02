BEGIN;

CREATE TABLE IF NOT EXISTS minute_ma_integrated_ws_connection (
    connection_id UUID PRIMARY KEY,
    collector_instance_id UUID NOT NULL,
    connected_at TIMESTAMP(6) NOT NULL,
    disconnected_at TIMESTAMP(6),
    reconnect_flag BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(16) NOT NULL,
    close_reason TEXT,
    last_receive_sequence BIGINT NOT NULL DEFAULT 0,
    subscriptions JSONB NOT NULL DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS raw_minute_ma_integrated_execution (
    received_at TIMESTAMP(6) NOT NULL,
    source_event_time TIMESTAMP(6) NOT NULL,
    business_date DATE NOT NULL,
    stock_code VARCHAR(6) NOT NULL CHECK (stock_code IN ('005930','000660')),
    trading_venue VARCHAR(16) NOT NULL DEFAULT 'INTEGRATED'
        CHECK (trading_venue='INTEGRATED'),
    tr_id VARCHAR(16) NOT NULL DEFAULT 'H0UNCNT0' CHECK (tr_id='H0UNCNT0'),
    connection_id UUID NOT NULL REFERENCES minute_ma_integrated_ws_connection(connection_id),
    collector_instance_id UUID NOT NULL,
    receive_sequence BIGINT NOT NULL,
    event_index INTEGER NOT NULL CHECK (event_index>=0),
    reconnect_flag BOOLEAN NOT NULL DEFAULT FALSE,
    source_gap_flag BOOLEAN NOT NULL DEFAULT FALSE,
    event_time_regression_flag BOOLEAN NOT NULL DEFAULT FALSE,
    duplicate_flag BOOLEAN NOT NULL DEFAULT FALSE,
    payload_hash VARCHAR(64) NOT NULL,
    current_price BIGINT NOT NULL,
    execution_volume BIGINT,
    accumulated_volume BIGINT NOT NULL,
    accumulated_amount NUMERIC(24,4),
    raw_values JSONB NOT NULL,
    raw_payload TEXT NOT NULL,
    PRIMARY KEY(received_at,connection_id,receive_sequence,event_index)
);
SELECT create_hypertable('raw_minute_ma_integrated_execution','received_at',if_not_exists=>TRUE);
CREATE INDEX IF NOT EXISTS idx_raw_minute_ma_integrated_execution_code_event
    ON raw_minute_ma_integrated_execution(stock_code,source_event_time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_minute_ma_integrated_execution_identity
    ON raw_minute_ma_integrated_execution(connection_id,receive_sequence,event_index);
CREATE INDEX IF NOT EXISTS idx_raw_minute_ma_integrated_execution_hash
    ON raw_minute_ma_integrated_execution(payload_hash,received_at DESC);

CREATE TABLE IF NOT EXISTS minute_ma_integrated_realtime_minute_bar (
    bar_time TIMESTAMP(0) NOT NULL,
    stock_code VARCHAR(6) NOT NULL CHECK (stock_code IN ('005930','000660')),
    trading_venue VARCHAR(16) NOT NULL DEFAULT 'INTEGRATED'
        CHECK (trading_venue='INTEGRATED'),
    open_price BIGINT NOT NULL,
    high_price BIGINT NOT NULL,
    low_price BIGINT NOT NULL,
    close_price BIGINT NOT NULL,
    volume BIGINT,
    execution_volume_sum BIGINT,
    first_accumulated_volume BIGINT,
    last_accumulated_volume BIGINT,
    event_count INTEGER NOT NULL,
    message_count INTEGER NOT NULL,
    first_source_event_time TIMESTAMP(6) NOT NULL,
    last_source_event_time TIMESTAMP(6) NOT NULL,
    first_received_at TIMESTAMP(6) NOT NULL,
    last_received_at TIMESTAMP(6) NOT NULL,
    finalized_at TIMESTAMP(6) NOT NULL,
    finalize_reason VARCHAR(32) NOT NULL,
    watermark_delay_ms INTEGER NOT NULL,
    connection_count INTEGER NOT NULL,
    reconnect_flag BOOLEAN NOT NULL DEFAULT FALSE,
    source_gap_flag BOOLEAN NOT NULL DEFAULT FALSE,
    event_time_regression_flag BOOLEAN NOT NULL DEFAULT FALSE,
    accumulated_volume_regression BOOLEAN NOT NULL DEFAULT FALSE,
    ordering_invariant_failure BOOLEAN NOT NULL DEFAULT FALSE,
    duplicate_excluded_count INTEGER NOT NULL DEFAULT 0,
    quality_status VARCHAR(16) NOT NULL,
    quality_reasons TEXT[] NOT NULL DEFAULT '{}',
    raw_source VARCHAR(32) NOT NULL DEFAULT 'KIS_H0UNCNT0_INTEGRATED',
    PRIMARY KEY(bar_time,stock_code,trading_venue)
);
SELECT create_hypertable('minute_ma_integrated_realtime_minute_bar','bar_time',if_not_exists=>TRUE);
CREATE INDEX IF NOT EXISTS idx_minute_ma_integrated_realtime_bar_code_finalized
    ON minute_ma_integrated_realtime_minute_bar(stock_code,finalized_at DESC);

COMMENT ON TABLE raw_minute_ma_integrated_execution IS
  'Minute MA signal-only KIS H0UNCNT0 INTEGRATED execution L0; independent of FLOW KRX RAW';
COMMENT ON TABLE minute_ma_integrated_realtime_minute_bar IS
  'Minute MA signal-only completed INTEGRATED 1MIN bars built from H0UNCNT0';

COMMIT;
