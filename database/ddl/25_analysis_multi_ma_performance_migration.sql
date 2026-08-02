-- Existing validation databases may contain the initial multi-MA draft schema.
-- This migration only adds compatibility columns/defaults; it never drops data.
ALTER TABLE IF EXISTS analysis_multi_ma_state ADD COLUMN IF NOT EXISTS observation_code VARCHAR(10);
UPDATE analysis_multi_ma_state SET observation_code=analysis_slot WHERE observation_code IS NULL;
ALTER TABLE IF EXISTS analysis_multi_ma_state ALTER COLUMN observation_code SET DEFAULT 'COMPLETE';
ALTER TABLE IF EXISTS analysis_multi_ma_state ADD COLUMN IF NOT EXISTS trade_date DATE;
ALTER TABLE IF EXISTS analysis_multi_ma_state ADD COLUMN IF NOT EXISTS position_direction VARCHAR(10) NOT NULL DEFAULT 'FLAT';
ALTER TABLE IF EXISTS analysis_multi_ma_state ADD COLUMN IF NOT EXISTS position_weight NUMERIC(8,6) NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_state ADD COLUMN IF NOT EXISTS applied_signals JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE IF EXISTS analysis_multi_ma_signal ADD COLUMN IF NOT EXISTS trade_date DATE;
ALTER TABLE IF EXISTS analysis_multi_ma_signal ADD COLUMN IF NOT EXISTS observation_code VARCHAR(10);
ALTER TABLE IF EXISTS analysis_multi_ma_signal ADD COLUMN IF NOT EXISTS signal_no VARCHAR(20);
ALTER TABLE IF EXISTS analysis_multi_ma_signal ADD COLUMN IF NOT EXISTS reason VARCHAR(500);
ALTER TABLE IF EXISTS analysis_multi_ma_signal ADD COLUMN IF NOT EXISTS market_code VARCHAR(20) NOT NULL DEFAULT 'KOSPI';
ALTER TABLE IF EXISTS analysis_multi_ma_signal ADD COLUMN IF NOT EXISTS analysis_slot VARCHAR(20) NOT NULL DEFAULT 'COMPLETE';
ALTER TABLE IF EXISTS analysis_multi_ma_signal ADD COLUMN IF NOT EXISTS ma_short NUMERIC(22,8) NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_signal ADD COLUMN IF NOT EXISTS ma_mid NUMERIC(22,8) NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_signal ADD COLUMN IF NOT EXISTS ma_long NUMERIC(22,8) NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_signal ADD COLUMN IF NOT EXISTS short_slope NUMERIC(22,8) NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_signal ALTER COLUMN trade_date SET DEFAULT CURRENT_DATE;
ALTER TABLE IF EXISTS analysis_multi_ma_signal ALTER COLUMN observation_code SET DEFAULT 'COMPLETE';
ALTER TABLE IF EXISTS analysis_multi_ma_signal ALTER COLUMN signal_no SET DEFAULT 'SIGNAL_1';
ALTER TABLE IF EXISTS analysis_multi_ma_signal ALTER COLUMN reason SET DEFAULT '';
ALTER TABLE IF EXISTS analysis_multi_ma_signal ALTER COLUMN signal_type SET DEFAULT 'SIGNAL_1';
ALTER TABLE IF EXISTS analysis_multi_ma_signal ALTER COLUMN market_code SET DEFAULT 'KOSPI';
ALTER TABLE IF EXISTS analysis_multi_ma_signal ALTER COLUMN analysis_slot SET DEFAULT 'COMPLETE';
ALTER TABLE IF EXISTS analysis_multi_ma_signal ALTER COLUMN ma_short SET DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_signal ALTER COLUMN ma_mid SET DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_signal ALTER COLUMN ma_long SET DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS uq_multi_ma_signal_replay ON analysis_multi_ma_signal
 (trade_date,stock_code,trading_venue,strategy_code,observation_code,ma_config_code,price_field_code,signal_time,signal_no,direction);

ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS trade_date DATE;
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS observation_code VARCHAR(10);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS ma_config_code VARCHAR(100);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS price_field_code VARCHAR(100);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS cycle_no INTEGER;
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS entry_ratio NUMERIC(8,6);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS average_entry_price NUMERIC(18,6);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS exit_time TIMESTAMP(3);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS exit_price NUMERIC(18,2);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS exit_type VARCHAR(20);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(30);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS realized_profit_amount NUMERIC(22,6);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS realized_profit_rate NUMERIC(18,10);
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS status VARCHAR(10) NOT NULL DEFAULT 'OPEN';
ALTER TABLE IF EXISTS analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN trade_date SET DEFAULT CURRENT_DATE;
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN observation_code SET DEFAULT 'COMPLETE';
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN ma_config_code SET DEFAULT 'MA_3_5_10';
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN price_field_code SET DEFAULT 'CLOSE';
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN cycle_no SET DEFAULT 1;
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN entry_ratio SET DEFAULT 1;
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN average_entry_price SET DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN market_code SET DEFAULT 'KOSPI';
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN analysis_slot SET DEFAULT 'COMPLETE';
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN cycle_id SET DEFAULT '00000000-0000-0000-0000-000000000000'::uuid;
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN entry_weight SET DEFAULT 1;
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN cumulative_weight SET DEFAULT 1;
ALTER TABLE IF EXISTS analysis_multi_ma_trade ALTER COLUMN detail_reason SET DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_multi_ma_cycle_natural ON analysis_multi_ma_trade
 (trade_date,stock_code,trading_venue,strategy_code,observation_code,ma_config_code,price_field_code,cycle_no);
CREATE UNIQUE INDEX IF NOT EXISTS uq_multi_ma_open_trade_settings ON analysis_multi_ma_trade
 (trade_date,stock_code,trading_venue,strategy_code,observation_code,ma_config_code,price_field_code) WHERE status='OPEN';

CREATE TABLE IF NOT EXISTS analysis_multi_ma_trade_leg (
 trade_id BIGINT NOT NULL REFERENCES analysis_multi_ma_trade(trade_id), signal_no VARCHAR(20) NOT NULL,
 signal_time TIMESTAMP(3) NOT NULL, entry_price NUMERIC(18,2) NOT NULL, entry_ratio NUMERIC(8,6) NOT NULL,
 notional_amount NUMERIC(22,6), created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(trade_id,signal_no));

ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS total_profit_amount NUMERIC(22,6) NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS total_profit_rate NUMERIC(18,10) NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS loss_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS win_rate NUMERIC(18,10) NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS signal_exit_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS session_close_exit_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS signal_exit_profit NUMERIC(22,6) NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS session_close_exit_profit NUMERIC(22,6) NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS max_profit NUMERIC(22,6);
ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS max_loss NUMERIC(22,6);
ALTER TABLE IF EXISTS analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS observation_code VARCHAR(10);
ALTER TABLE IF EXISTS analysis_multi_ma_summary ALTER COLUMN market_code SET DEFAULT 'KOSPI';
ALTER TABLE IF EXISTS analysis_multi_ma_summary ALTER COLUMN analysis_slot SET DEFAULT 'COMPLETE';
ALTER TABLE IF EXISTS analysis_multi_ma_summary ALTER COLUMN observation_code SET DEFAULT 'COMPLETE';
ALTER TABLE IF EXISTS analysis_multi_ma_summary ALTER COLUMN ma_config_code SET DEFAULT 'MA_3_5_10';
ALTER TABLE IF EXISTS analysis_multi_ma_summary ALTER COLUMN price_field_code SET DEFAULT 'CLOSE';
CREATE UNIQUE INDEX IF NOT EXISTS uq_multi_ma_summary_natural ON analysis_multi_ma_summary
 (trade_date,stock_code,trading_venue,strategy_code,observation_code,ma_config_code,price_field_code);
