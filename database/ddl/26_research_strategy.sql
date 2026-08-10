-- Non-destructive research-only persistence.  RAW and live analysis tables are never changed.
CREATE TABLE IF NOT EXISTS research_run (
    run_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    observation_code VARCHAR(20) NOT NULL DEFAULT 'COMPLETE',
    initial_capital NUMERIC(20,2) NOT NULL DEFAULT 10000000,
    fee_rate NUMERIC(12,8) NOT NULL DEFAULT 0,
    slippage_rate NUMERIC(12,8) NOT NULL DEFAULT 0,
    cost_policy_version VARCHAR(100) NOT NULL DEFAULT 'UNSPECIFIED',
    status VARCHAR(20) NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS research_feature (
    run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    trading_date DATE NOT NULL, stock_code VARCHAR(20) NOT NULL,
    observation_code VARCHAR(20) NOT NULL, observation_time TIMESTAMP NOT NULL,
    price NUMERIC(18,2) NOT NULL, ma3 NUMERIC(18,8), ma5 NUMERIC(18,8), ma10 NUMERIC(18,8), ma20 NUMERIC(18,8),
    ma10_direction VARCHAR(10), data_status VARCHAR(30) NOT NULL,
    PRIMARY KEY (run_id, stock_code, observation_code, observation_time)
);

CREATE TABLE IF NOT EXISTS research_signal_event (
    event_id BIGSERIAL PRIMARY KEY, run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    trading_date DATE NOT NULL, signal_source_stock_code VARCHAR(20) NOT NULL,
    observation_code VARCHAR(20) NOT NULL, signal_time TIMESTAMP NOT NULL,
    strategy_code VARCHAR(30) NOT NULL, signal_type VARCHAR(30) NOT NULL, direction VARCHAR(10) NOT NULL,
    signal_price NUMERIC(18,2) NOT NULL, ma3 NUMERIC(18,8), ma5 NUMERIC(18,8), ma10 NUMERIC(18,8),
    ma10_direction VARCHAR(10), pending_yn CHAR(1) NOT NULL DEFAULT 'N', pending_started_at TIMESTAMP,
    confirm_time TIMESTAMP, session_code VARCHAR(30), data_status VARCHAR(30) NOT NULL,
    UNIQUE(run_id, signal_source_stock_code, observation_code, signal_time, strategy_code, signal_type, direction)
);

CREATE TABLE IF NOT EXISTS research_trade_cycle (
    cycle_id BIGSERIAL PRIMARY KEY, run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    trading_date DATE NOT NULL, trade_stock_code VARCHAR(20) NOT NULL, signal_source_stock_code VARCHAR(20) NOT NULL,
    exit_signal_source_stock_code VARCHAR(20), strategy_code VARCHAR(30) NOT NULL, observation_code VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL, entry_signal_time TIMESTAMP, entry_confirm_time TIMESTAMP, entry_time TIMESTAMP NOT NULL,
    entry_price NUMERIC(18,2) NOT NULL, exit_signal_time TIMESTAMP, exit_time TIMESTAMP, exit_price NUMERIC(18,2),
    exit_type VARCHAR(30), quantity BIGINT NOT NULL, invested_amount NUMERIC(20,2) NOT NULL,
    realized_profit NUMERIC(20,2), invested_return_rate NUMERIC(18,8), capital_return_rate NUMERIC(18,8),
    gross_realized_profit NUMERIC(20,2), buy_fee NUMERIC(20,2) NOT NULL DEFAULT 0,
    sell_fee NUMERIC(20,2) NOT NULL DEFAULT 0, sell_tax NUMERIC(20,2) NOT NULL DEFAULT 0,
    total_trading_cost NUMERIC(20,2) NOT NULL DEFAULT 0,
    holding_seconds BIGINT, data_status VARCHAR(30) NOT NULL DEFAULT 'NORMAL',
    UNIQUE(run_id, trade_stock_code, signal_source_stock_code, strategy_code, observation_code, entry_time)
);

CREATE TABLE IF NOT EXISTS research_trade_leg (
    cycle_id BIGINT NOT NULL REFERENCES research_trade_cycle(cycle_id) ON DELETE CASCADE,
    signal_type VARCHAR(30) NOT NULL, entry_time TIMESTAMP NOT NULL, entry_price NUMERIC(18,2) NOT NULL,
    entry_ratio NUMERIC(12,8) NOT NULL, quantity BIGINT NOT NULL, invested_amount NUMERIC(20,2) NOT NULL,
    PRIMARY KEY(cycle_id, signal_type)
);

CREATE TABLE IF NOT EXISTS research_performance_daily (
    run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    trading_date DATE NOT NULL, trade_stock_code VARCHAR(20) NOT NULL,
    signal_source_stock_code VARCHAR(20) NOT NULL, strategy_code VARCHAR(30) NOT NULL,
    observation_code VARCHAR(20) NOT NULL, direction VARCHAR(10) NOT NULL,
    session_code VARCHAR(30), daily_return_rate NUMERIC(18,8), daily_market_direction VARCHAR(10),
    closed_count INTEGER NOT NULL DEFAULT 0, win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0, flat_count INTEGER NOT NULL DEFAULT 0,
    realized_profit NUMERIC(20,2) NOT NULL DEFAULT 0, invested_amount NUMERIC(20,2) NOT NULL DEFAULT 0,
    invested_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0, capital_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0,
    avg_trade_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0, avg_holding_seconds NUMERIC(20,2) NOT NULL DEFAULT 0,
    signal_exit_profit NUMERIC(20,2) NOT NULL DEFAULT 0, session_close_profit NUMERIC(20,2) NOT NULL DEFAULT 0,
    PRIMARY KEY(run_id,trading_date,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction,session_code)
);

CREATE TABLE IF NOT EXISTS research_performance_period (
    run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    start_date DATE NOT NULL, end_date DATE NOT NULL, trade_stock_code VARCHAR(20) NOT NULL,
    signal_source_stock_code VARCHAR(20) NOT NULL, strategy_code VARCHAR(30) NOT NULL,
    observation_code VARCHAR(20) NOT NULL, direction VARCHAR(10) NOT NULL,
    closed_count INTEGER NOT NULL DEFAULT 0, win_count INTEGER NOT NULL DEFAULT 0,
    loss_count INTEGER NOT NULL DEFAULT 0, flat_count INTEGER NOT NULL DEFAULT 0,
    realized_profit NUMERIC(20,2) NOT NULL DEFAULT 0, invested_amount NUMERIC(20,2) NOT NULL DEFAULT 0,
    invested_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0, capital_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0,
    avg_trade_return_rate NUMERIC(18,8) NOT NULL DEFAULT 0, avg_holding_seconds NUMERIC(20,2) NOT NULL DEFAULT 0,
    signal_exit_profit NUMERIC(20,2) NOT NULL DEFAULT 0, session_close_profit NUMERIC(20,2) NOT NULL DEFAULT 0,
    PRIMARY KEY(run_id,start_date,end_date,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction)
);

CREATE TABLE IF NOT EXISTS research_position_daily (
    run_id UUID NOT NULL REFERENCES research_run(run_id) ON DELETE CASCADE,
    cycle_id BIGINT NOT NULL REFERENCES research_trade_cycle(cycle_id) ON DELETE CASCADE,
    trading_date DATE NOT NULL, trade_stock_code VARCHAR(20) NOT NULL,
    signal_source_stock_code VARCHAR(20) NOT NULL, strategy_code VARCHAR(30) NOT NULL,
    observation_code VARCHAR(20) NOT NULL, direction VARCHAR(10) NOT NULL,
    entry_date DATE NOT NULL, entry_price NUMERIC(18,2) NOT NULL,
    valuation_close_price NUMERIC(18,2) NOT NULL, quantity BIGINT NOT NULL,
    invested_amount NUMERIC(20,2) NOT NULL, unrealized_profit NUMERIC(20,2) NOT NULL,
    unrealized_return_rate NUMERIC(18,8) NOT NULL, capital_return_rate NUMERIC(18,8) NOT NULL,
    position_status VARCHAR(20) NOT NULL,
    PRIMARY KEY(run_id,cycle_id,trading_date)
);

ALTER TABLE research_run ADD COLUMN IF NOT EXISTS cost_policy_version VARCHAR(100) NOT NULL DEFAULT 'UNSPECIFIED';
ALTER TABLE research_trade_cycle ADD COLUMN IF NOT EXISTS gross_realized_profit NUMERIC(20,2);
ALTER TABLE research_trade_cycle ADD COLUMN IF NOT EXISTS buy_fee NUMERIC(20,2) NOT NULL DEFAULT 0;
ALTER TABLE research_trade_cycle ADD COLUMN IF NOT EXISTS sell_fee NUMERIC(20,2) NOT NULL DEFAULT 0;
ALTER TABLE research_trade_cycle ADD COLUMN IF NOT EXISTS sell_tax NUMERIC(20,2) NOT NULL DEFAULT 0;
ALTER TABLE research_trade_cycle ADD COLUMN IF NOT EXISTS total_trading_cost NUMERIC(20,2) NOT NULL DEFAULT 0;
ALTER TABLE research_feature ADD COLUMN IF NOT EXISTS ma20 NUMERIC(18,8);

COMMENT ON TABLE research_run IS '연구 재생 실행 단위. 비용 정책 snapshot과 기간을 고정한다.';
COMMENT ON TABLE research_feature IS 'RAW 공식 시장 데이터에서 재생 가능한 파생 feature. RAW를 대체하지 않는다.';
COMMENT ON TABLE research_signal_event IS 'canonical signal 및 MA10_CONFIRM pending/confirm 상태 이력.';
COMMENT ON TABLE research_trade_cycle IS '전략별 진입부터 청산까지의 가상 거래 cycle. realized_profit은 비용 차감 후 순손익.';
COMMENT ON TABLE research_trade_leg IS 'ACCUMULATED cycle을 구성하는 개별 분할 진입 leg.';
COMMENT ON TABLE research_performance_daily IS '청산 완료 cycle만 집계한 일별 실현성과 summary. 평가손익은 포함하지 않는다.';
COMMENT ON TABLE research_performance_period IS 'DEPRECATED. 신규 사용 금지. 기간 성과는 research_performance_daily 또는 cycle에서 동적으로 집계한다.';
COMMENT ON TABLE research_position_daily IS '일봉 전략 OPEN 포지션의 진입가 대비 일별 mark-to-market 평가 기록.';
COMMENT ON COLUMN research_run.parameters IS 'run에서 실제 사용한 수수료·세금·슬리피지 정책 snapshot JSON.';
COMMENT ON COLUMN research_run.cost_policy_version IS '거래비용 정책 버전.';
COMMENT ON COLUMN research_trade_cycle.gross_realized_profit IS '매수·매도 수수료와 세금 차감 전 총실현손익.';
COMMENT ON COLUMN research_trade_cycle.realized_profit IS '총실현손익에서 buy_fee, sell_fee, sell_tax를 차감한 순실현손익.';
COMMENT ON COLUMN research_trade_cycle.total_trading_cost IS 'buy_fee + sell_fee + sell_tax.';
COMMENT ON COLUMN research_signal_event.pending_yn IS 'MA10_CONFIRM 대기 신호 여부.';
COMMENT ON COLUMN research_signal_event.confirm_time IS 'MA10_CONFIRM 조건이 충족된 최초 시각.';

CREATE INDEX IF NOT EXISTS ix_research_feature_run_stock_time ON research_feature(run_id, stock_code, observation_time);
CREATE INDEX IF NOT EXISTS ix_research_cycle_run_time ON research_trade_cycle(run_id, entry_time);
