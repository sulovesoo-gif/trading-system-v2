-- Minute MA LONG + FLOW REAL F1/F2/F3 PAPER research (additive only).
BEGIN;

CREATE TABLE IF NOT EXISTS minute_ma_real_variant (
    real_variant_id BIGSERIAL PRIMARY KEY,
    minute_strategy_id BIGINT NOT NULL REFERENCES minute_ma_strategy_master(minute_strategy_id),
    strategy_id TEXT NOT NULL,
    signal_code VARCHAR(20) NOT NULL,
    filter_code VARCHAR(16) NOT NULL CHECK (filter_code IN ('REAL_F1','REAL_F2','REAL_F3')),
    paper_epoch INTEGER NOT NULL,
    k_mode INTEGER NOT NULL CHECK (k_mode > 0),
    initial_capital NUMERIC(24,8) NOT NULL CHECK (initial_capital=10000000),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    effective_from DATE NOT NULL,
    effective_to DATE,
    last_source_bar_time TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(minute_strategy_id,filter_code,paper_epoch),
    CHECK (effective_to IS NULL OR effective_to >= effective_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_mm_real_variant_active
  ON minute_ma_real_variant(minute_strategy_id,filter_code) WHERE effective_to IS NULL;

CREATE TABLE IF NOT EXISTS minute_ma_real_paper_trade (
    real_paper_trade_id BIGSERIAL PRIMARY KEY,
    real_variant_id BIGINT NOT NULL REFERENCES minute_ma_real_variant(real_variant_id),
    minute_strategy_id BIGINT NOT NULL REFERENCES minute_ma_strategy_master(minute_strategy_id),
    strategy_id TEXT NOT NULL,
    signal_code VARCHAR(20) NOT NULL,
    filter_code VARCHAR(16) NOT NULL CHECK (filter_code IN ('REAL_F1','REAL_F2','REAL_F3')),
    paper_epoch INTEGER NOT NULL,
    lifecycle_status VARCHAR(24) NOT NULL CHECK (lifecycle_status IN ('OPEN','CLOSED','SKIPPED_NO_SLOT','SKIPPED_QTY_ZERO')),
    entry_signal_key CHAR(64) NOT NULL,
    entry_signal_time TIMESTAMP NOT NULL,
    entry_execution_time TIMESTAMP NOT NULL,
    entry_price NUMERIC(24,8) NOT NULL CHECK (entry_price>0),
    exit_signal_time TIMESTAMP,
    exit_execution_time TIMESTAMP,
    exit_price NUMERIC(24,8),
    exit_reason VARCHAR(24) CHECK (exit_reason IS NULL OR exit_reason='NORMAL_EXIT'),
    slot_no INTEGER,
    quantity BIGINT NOT NULL DEFAULT 0 CHECK (quantity>=0),
    capital_before NUMERIC(24,8),
    capital_after NUMERIC(24,8),
    gross_return NUMERIC(24,12),
    buy_fee NUMERIC(24,8),
    sell_fee NUMERIC(24,8),
    sell_tax NUMERIC(24,8),
    total_cost NUMERIC(24,8),
    net_return NUMERIC(24,12),
    realized_pnl NUMERIC(24,8),
    velocity_value NUMERIC,
    velocity_avg_3 NUMERIC,
    velocity_avg_10 NUMERIC,
    flow_avg_5 NUMERIC,
    flow_avg_20 NUMERIC,
    real_is_complete BOOLEAN NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(real_variant_id,entry_signal_key),
    CHECK (entry_signal_time::time BETWEEN TIME '15:00' AND TIME '15:18'),
    CHECK (exit_reason IS NULL OR exit_reason='NORMAL_EXIT'),
    CHECK ((lifecycle_status='CLOSED' AND exit_execution_time IS NOT NULL AND capital_after IS NOT NULL)
        OR lifecycle_status<>'CLOSED')
);
CREATE INDEX IF NOT EXISTS ix_mm_real_trade_variant_entry
  ON minute_ma_real_paper_trade(real_variant_id,entry_execution_time);
CREATE INDEX IF NOT EXISTS ix_mm_real_trade_open
  ON minute_ma_real_paper_trade(minute_strategy_id) WHERE lifecycle_status='OPEN';

CREATE TABLE IF NOT EXISTS minute_ma_real_paper_slot (
    real_variant_id BIGINT NOT NULL REFERENCES minute_ma_real_variant(real_variant_id),
    paper_epoch INTEGER NOT NULL,
    slot_no INTEGER NOT NULL CHECK (slot_no>0),
    current_capital NUMERIC(24,8) NOT NULL CHECK (current_capital>=0),
    occupancy_status VARCHAR(8) NOT NULL CHECK (occupancy_status IN ('FREE','OPEN')),
    current_trade_id BIGINT REFERENCES minute_ma_real_paper_trade(real_paper_trade_id),
    version BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(real_variant_id,paper_epoch,slot_no),
    CHECK ((occupancy_status='FREE' AND current_trade_id IS NULL)
        OR (occupancy_status='OPEN' AND current_trade_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS minute_ma_real_daily_snapshot (
    snapshot_date DATE NOT NULL,
    real_variant_id BIGINT NOT NULL REFERENCES minute_ma_real_variant(real_variant_id),
    strategy_id TEXT NOT NULL,
    signal_code VARCHAR(20) NOT NULL,
    filter_code VARCHAR(16) NOT NULL,
    k_mode INTEGER NOT NULL,
    current_capital NUMERIC(24,8) NOT NULL,
    trade_count BIGINT NOT NULL,
    win_count BIGINT NOT NULL,
    win_rate NUMERIC(18,10) NOT NULL,
    average_net_profit NUMERIC(24,8) NOT NULL,
    max_drawdown NUMERIC(18,10) NOT NULL,
    cumulative_profit NUMERIC(24,8) NOT NULL,
    cumulative_return NUMERIC(18,10) NOT NULL,
    recent20_profit NUMERIC(24,8) NOT NULL,
    recent20_return NUMERIC(18,10) NOT NULL,
    month_profit NUMERIC(24,8) NOT NULL,
    month_return NUMERIC(18,10) NOT NULL,
    week_profit NUMERIC(24,8) NOT NULL,
    week_return NUMERIC(18,10) NOT NULL,
    day_profit NUMERIC(24,8) NOT NULL,
    day_return NUMERIC(18,10) NOT NULL,
    cumulative_rank INTEGER NOT NULL,
    recent20_rank INTEGER NOT NULL,
    month_rank INTEGER NOT NULL,
    week_rank INTEGER NOT NULL,
    day_rank INTEGER NOT NULL,
    rank_delta_day INTEGER,
    rank_delta_week INTEGER,
    rank_delta_month INTEGER,
    profit_delta_day NUMERIC(24,8),
    profit_delta_week NUMERIC(24,8),
    profit_delta_month NUMERIC(24,8),
    calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(snapshot_date,real_variant_id)
);
CREATE INDEX IF NOT EXISTS ix_mm_real_daily_filter_rank
  ON minute_ma_real_daily_snapshot(snapshot_date,filter_code,cumulative_rank);

CREATE TABLE IF NOT EXISTS minute_ma_real_candidate_snapshot (
    snapshot_date DATE NOT NULL,
    real_variant_id BIGINT NOT NULL REFERENCES minute_ma_real_variant(real_variant_id),
    strategy_id TEXT NOT NULL,
    filter_code VARCHAR(16) NOT NULL,
    inclusion_reasons TEXT[] NOT NULL,
    prior_candidate BOOLEAN NOT NULL DEFAULT FALSE,
    candidate_state VARCHAR(8) NOT NULL CHECK (candidate_state IN ('ENTERED','STAYED','EXITED')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(snapshot_date,real_variant_id)
);
CREATE INDEX IF NOT EXISTS ix_mm_real_candidate_current
  ON minute_ma_real_candidate_snapshot(snapshot_date,filter_code,strategy_id);

COMMENT ON TABLE minute_ma_real_variant IS 'Independent 10M K_MODE PAPER epochs for LONG Minute-MA x REAL F1/F2/F3';
COMMENT ON TABLE minute_ma_real_paper_trade IS 'Normal-MA-exit-only PAPER ledger; STOP/EOD exits are forbidden by CHECK';
COMMENT ON TABLE minute_ma_real_paper_slot IS 'Per-epoch K_MODE slot capital and occupancy';

COMMIT;
