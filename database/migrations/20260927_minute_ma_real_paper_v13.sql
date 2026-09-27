-- Minute MA + REAL PAPER V1.3: overlapping entries and parallel fixed-10M accounting.
-- The V1.1 research tables must still be empty because no backfill/runtime was run.
BEGIN;

DO $$
DECLARE
    v_name text;
    v_count bigint;
BEGIN
    FOREACH v_name IN ARRAY ARRAY[
        'minute_ma_real_variant','minute_ma_real_paper_trade',
        'minute_ma_real_paper_slot','minute_ma_real_daily_snapshot',
        'minute_ma_real_candidate_snapshot'
    ] LOOP
        EXECUTE format('SELECT count(*) FROM %I',v_name) INTO v_count;
        IF v_count <> 0 THEN
            RAISE EXCEPTION 'V1.3 conversion requires empty %, found % rows',v_name,v_count;
        END IF;
    END LOOP;
END $$;

DROP TABLE minute_ma_real_candidate_snapshot;
DROP TABLE minute_ma_real_daily_snapshot;
DROP TABLE minute_ma_real_paper_slot;

ALTER TABLE minute_ma_real_variant DROP COLUMN k_mode;
COMMENT ON TABLE minute_ma_real_variant IS
  'Independent 10M realized-capital PAPER epochs for LONG Minute-MA x REAL F1/F2/F3';

ALTER TABLE minute_ma_real_paper_trade
  DROP CONSTRAINT minute_ma_real_paper_trade_lifecycle_status_check,
  DROP CONSTRAINT minute_ma_real_paper_trade_check,
  DROP COLUMN slot_no;
ALTER TABLE minute_ma_real_paper_trade
  RENAME COLUMN quantity TO compound_quantity;
ALTER TABLE minute_ma_real_paper_trade
  RENAME COLUMN capital_before TO entry_realized_capital;
ALTER TABLE minute_ma_real_paper_trade
  RENAME COLUMN capital_after TO settlement_realized_capital_after;
ALTER TABLE minute_ma_real_paper_trade
  RENAME COLUMN gross_return TO compound_gross_return;
ALTER TABLE minute_ma_real_paper_trade
  RENAME COLUMN buy_fee TO compound_buy_fee;
ALTER TABLE minute_ma_real_paper_trade
  RENAME COLUMN sell_fee TO compound_sell_fee;
ALTER TABLE minute_ma_real_paper_trade
  RENAME COLUMN sell_tax TO compound_sell_tax;
ALTER TABLE minute_ma_real_paper_trade
  RENAME COLUMN total_cost TO compound_total_cost;
ALTER TABLE minute_ma_real_paper_trade
  RENAME COLUMN net_return TO compound_net_return;
ALTER TABLE minute_ma_real_paper_trade
  RENAME COLUMN realized_pnl TO compound_realized_pnl;
ALTER TABLE minute_ma_real_paper_trade
  ADD COLUMN compound_gross_pnl NUMERIC(24,8),
  ADD COLUMN fixed_quantity BIGINT NOT NULL DEFAULT 0 CHECK (fixed_quantity>=0),
  ADD COLUMN fixed_gross_pnl NUMERIC(24,8),
  ADD COLUMN fixed_gross_return NUMERIC(24,12),
  ADD COLUMN fixed_buy_fee NUMERIC(24,8),
  ADD COLUMN fixed_sell_fee NUMERIC(24,8),
  ADD COLUMN fixed_sell_tax NUMERIC(24,8),
  ADD COLUMN fixed_total_cost NUMERIC(24,8),
  ADD COLUMN fixed_net_return NUMERIC(24,12),
  ADD COLUMN fixed_realized_pnl NUMERIC(24,8),
  ADD COLUMN settlement_time TIMESTAMP,
  ADD CONSTRAINT minute_ma_real_paper_trade_lifecycle_status_check
    CHECK (lifecycle_status IN ('OPEN','CLOSED')),
  ADD CONSTRAINT minute_ma_real_paper_trade_closed_check
    CHECK ((lifecycle_status='CLOSED' AND exit_execution_time IS NOT NULL
            AND settlement_time IS NOT NULL
            AND settlement_realized_capital_after IS NOT NULL)
        OR lifecycle_status='OPEN');

CREATE TABLE minute_ma_real_capital_epoch (
    real_capital_epoch_id BIGSERIAL PRIMARY KEY,
    real_variant_id BIGINT NOT NULL REFERENCES minute_ma_real_variant(real_variant_id),
    paper_epoch INTEGER NOT NULL,
    initial_capital NUMERIC(24,8) NOT NULL CHECK (initial_capital=10000000),
    current_realized_capital NUMERIC(24,8) NOT NULL,
    effective_from DATE NOT NULL,
    ended_at TIMESTAMP,
    reset_reason VARCHAR(64) NOT NULL DEFAULT 'INITIAL_V1_3',
    version BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(real_variant_id,paper_epoch),
    CHECK (ended_at IS NULL OR ended_at::date >= effective_from)
);
CREATE UNIQUE INDEX ux_mm_real_capital_epoch_active
  ON minute_ma_real_capital_epoch(real_variant_id) WHERE ended_at IS NULL;

CREATE TABLE minute_ma_real_daily_snapshot (
    snapshot_date DATE NOT NULL,
    real_variant_id BIGINT NOT NULL REFERENCES minute_ma_real_variant(real_variant_id),
    strategy_id TEXT NOT NULL,
    signal_code VARCHAR(20) NOT NULL,
    filter_code VARCHAR(16) NOT NULL,
    compound_current_capital NUMERIC(24,8) NOT NULL,
    trade_count BIGINT NOT NULL,
    win_count BIGINT NOT NULL,
    win_rate NUMERIC(18,10) NOT NULL,
    compound_average_net_profit NUMERIC(24,8) NOT NULL,
    fixed_average_net_profit NUMERIC(24,8) NOT NULL,
    compound_max_drawdown NUMERIC(18,10) NOT NULL,
    fixed_max_drawdown NUMERIC(18,10) NOT NULL,
    compound_cumulative_profit NUMERIC(24,8) NOT NULL,
    compound_cumulative_return NUMERIC(18,10) NOT NULL,
    compound_recent20_profit NUMERIC(24,8) NOT NULL,
    compound_recent20_return NUMERIC(18,10) NOT NULL,
    compound_month_profit NUMERIC(24,8) NOT NULL,
    compound_month_return NUMERIC(18,10) NOT NULL,
    compound_week_profit NUMERIC(24,8) NOT NULL,
    compound_week_return NUMERIC(18,10) NOT NULL,
    compound_day_profit NUMERIC(24,8) NOT NULL,
    compound_day_return NUMERIC(18,10) NOT NULL,
    fixed_cumulative_profit NUMERIC(24,8) NOT NULL,
    fixed_cumulative_return NUMERIC(18,10) NOT NULL,
    fixed_recent20_profit NUMERIC(24,8) NOT NULL,
    fixed_recent20_return NUMERIC(18,10) NOT NULL,
    fixed_month_profit NUMERIC(24,8) NOT NULL,
    fixed_month_return NUMERIC(18,10) NOT NULL,
    fixed_week_profit NUMERIC(24,8) NOT NULL,
    fixed_week_return NUMERIC(18,10) NOT NULL,
    fixed_day_profit NUMERIC(24,8) NOT NULL,
    fixed_day_return NUMERIC(18,10) NOT NULL,
    compound_cumulative_rank INTEGER NOT NULL,
    compound_recent20_rank INTEGER NOT NULL,
    compound_month_rank INTEGER NOT NULL,
    compound_week_rank INTEGER NOT NULL,
    compound_day_rank INTEGER NOT NULL,
    fixed_cumulative_rank INTEGER NOT NULL,
    fixed_recent20_rank INTEGER NOT NULL,
    fixed_month_rank INTEGER NOT NULL,
    fixed_week_rank INTEGER NOT NULL,
    fixed_day_rank INTEGER NOT NULL,
    compound_rank_delta_day INTEGER,
    compound_rank_delta_week INTEGER,
    compound_rank_delta_month INTEGER,
    compound_profit_delta_day NUMERIC(24,8),
    compound_profit_delta_week NUMERIC(24,8),
    compound_profit_delta_month NUMERIC(24,8),
    fixed_rank_delta_day INTEGER,
    fixed_rank_delta_week INTEGER,
    fixed_rank_delta_month INTEGER,
    fixed_profit_delta_day NUMERIC(24,8),
    fixed_profit_delta_week NUMERIC(24,8),
    fixed_profit_delta_month NUMERIC(24,8),
    calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(snapshot_date,real_variant_id)
);
CREATE INDEX ix_mm_real_daily_compound_rank
  ON minute_ma_real_daily_snapshot(snapshot_date,filter_code,compound_cumulative_rank);
CREATE INDEX ix_mm_real_daily_fixed_rank
  ON minute_ma_real_daily_snapshot(snapshot_date,filter_code,fixed_cumulative_rank);

CREATE TABLE minute_ma_real_candidate_snapshot (
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
CREATE INDEX ix_mm_real_candidate_current
  ON minute_ma_real_candidate_snapshot(snapshot_date,filter_code,strategy_id);

COMMENT ON TABLE minute_ma_real_capital_epoch IS
  'Confirmed realized-capital state; OPEN unrealized PnL and entry reservations are excluded';
COMMENT ON TABLE minute_ma_real_paper_trade IS
  'Independent overlapping NORMAL_EXIT PAPER trades with compound and fixed-10M accounting';

COMMIT;
