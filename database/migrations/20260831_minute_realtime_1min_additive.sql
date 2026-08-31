-- H0STCNT0-derived realtime 1MIN research bars.  This does not replace raw_stock_minute.
BEGIN;

CREATE TABLE IF NOT EXISTS flow_realtime_minute_bar (
    bar_time TIMESTAMP(0) NOT NULL,
    stock_code VARCHAR(20) NOT NULL,
    trading_venue VARCHAR(10) NOT NULL DEFAULT 'KRX' CHECK (trading_venue='KRX'),
    open_price BIGINT NOT NULL,
    high_price BIGINT NOT NULL,
    low_price BIGINT NOT NULL,
    close_price BIGINT NOT NULL,
    volume BIGINT,
    execution_volume_sum BIGINT NOT NULL,
    first_accumulated_volume BIGINT,
    last_accumulated_volume BIGINT,
    event_count BIGINT NOT NULL,
    message_count BIGINT NOT NULL,
    first_source_event_time TIMESTAMP(6) NOT NULL,
    last_source_event_time TIMESTAMP(6) NOT NULL,
    first_received_at TIMESTAMP(6) NOT NULL,
    last_received_at TIMESTAMP(6) NOT NULL,
    finalized_at TIMESTAMP(6) NOT NULL,
    finalize_reason VARCHAR(32) NOT NULL CHECK (finalize_reason IN ('NEXT_MINUTE_EVENT','GRACE_WATERMARK')),
    watermark_delay_ms NUMERIC(14,3) NOT NULL,
    connection_count INTEGER NOT NULL,
    reconnect_flag BOOLEAN NOT NULL,
    source_gap_flag BOOLEAN NOT NULL,
    event_time_regression_flag BOOLEAN NOT NULL,
    ordering_invariant_failure BOOLEAN NOT NULL,
    accumulated_volume_regression BOOLEAN NOT NULL,
    duplicate_excluded_count BIGINT NOT NULL,
    suspect_trade_count BIGINT NOT NULL DEFAULT 0,
    quality_status VARCHAR(16) NOT NULL CHECK (quality_status IN ('COMPLETE','SUSPECT','INCOMPLETE')),
    quality_reasons TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    raw_source VARCHAR(32) NOT NULL DEFAULT 'KIS_H0STCNT0' CHECK (raw_source='KIS_H0STCNT0'),
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (bar_time,stock_code,trading_venue)
);
SELECT create_hypertable('flow_realtime_minute_bar','bar_time',if_not_exists=>TRUE);
CREATE INDEX IF NOT EXISTS ix_flow_realtime_minute_code_time
  ON flow_realtime_minute_bar(stock_code,bar_time DESC);

CREATE TABLE IF NOT EXISTS flow_realtime_minute_rest_audit (
    bar_time TIMESTAMP(0) NOT NULL,
    stock_code VARCHAR(20) NOT NULL,
    trading_venue VARCHAR(10) NOT NULL DEFAULT 'KRX' CHECK (trading_venue='KRX'),
    ws_open_price BIGINT NOT NULL,
    ws_high_price BIGINT NOT NULL,
    ws_low_price BIGINT NOT NULL,
    ws_close_price BIGINT NOT NULL,
    ws_volume BIGINT,
    ws_finalized_at TIMESTAMP(6) NOT NULL,
    rest_open_price NUMERIC,
    rest_high_price NUMERIC,
    rest_low_price NUMERIC,
    rest_close_price NUMERIC,
    rest_volume BIGINT,
    rest_collected_at TIMESTAMP(6),
    open_match BOOLEAN,
    high_match BOOLEAN,
    low_match BOOLEAN,
    close_match BOOLEAN,
    volume_match BOOLEAN,
    comparison_status VARCHAR(24) NOT NULL CHECK (comparison_status IN ('REST_PENDING','MATCH','MISMATCH')),
    mismatch_fields TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    compared_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (bar_time,stock_code,trading_venue),
    FOREIGN KEY (bar_time,stock_code,trading_venue)
      REFERENCES flow_realtime_minute_bar(bar_time,stock_code,trading_venue)
);

CREATE TABLE IF NOT EXISTS minute_ma_policy_paper_pending_entry (
    pending_entry_id BIGSERIAL PRIMARY KEY,
    minute_policy_path_id BIGINT NOT NULL REFERENCES minute_ma_policy_path(minute_policy_path_id),
    signal_event_key CHAR(64) NOT NULL,
    source_bar_time TIMESTAMP NOT NULL,
    confirmed_at TIMESTAMP NOT NULL,
    proxy_bar_time TIMESTAMP NOT NULL,
    source_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    pending_reason VARCHAR(48) NOT NULL CHECK (pending_reason IN (
      'EXECUTION_PROXY_MISSING','UNDERLYING_PROXY_MISSING','BOTH_PROXIES_MISSING')),
    pending_status VARCHAR(16) NOT NULL DEFAULT 'PENDING' CHECK (pending_status IN ('PENDING','COMPLETED')),
    first_pending_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_checked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    UNIQUE(minute_policy_path_id,signal_event_key)
);
CREATE INDEX IF NOT EXISTS ix_minute_ma_policy_pending_open
  ON minute_ma_policy_paper_pending_entry(proxy_bar_time,minute_policy_path_id)
  WHERE pending_status='PENDING';

COMMENT ON TABLE flow_realtime_minute_bar IS
  'Immutable H0STCNT0-derived 1MIN research bars; not an approved Minute V1 runtime source';
COMMENT ON COLUMN flow_realtime_minute_bar.volume IS
  'Last ACML_VOL minus the immediately previous minute last ACML_VOL; NULL when the boundary is unsafe';
COMMENT ON TABLE flow_realtime_minute_rest_audit IS
  'Non-corrective comparison evidence between H0STCNT0 1MIN and later REST raw_stock_minute';
COMMENT ON TABLE minute_ma_policy_paper_pending_entry IS
  'Durable V1 PAPER entry signals waiting for next-minute execution/underlying OPEN proxies';

COMMIT;
