/* 공통코드 기반 12개 기준 초 다중 이동평균 분석. 기존 SMA 교차 테이블과 분리한다. */
SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS analysis_multi_ma_state
(
    stock_code                  VARCHAR(20)  NOT NULL,
    market_code                 VARCHAR(30)  NOT NULL,
    trading_venue               VARCHAR(10)  NOT NULL CHECK (trading_venue IN ('KRX', 'NXT', 'INTEGRATED')),
    strategy_code               VARCHAR(30)  NOT NULL,
    analysis_slot               VARCHAR(10)  NOT NULL,
    ma_config_code              VARCHAR(100) NOT NULL,
    price_field_code            VARCHAR(30)  NOT NULL,
    last_signal_time            TIMESTAMP(3),
    last_processed_time         TIMESTAMP(3),
    ma_short                    NUMERIC(18,6),
    ma_mid                      NUMERIC(18,6),
    ma_long                     NUMERIC(18,6),
    short_slope                 NUMERIC(18,6),
    previous_short_slope        NUMERIC(18,6),
    position_direction          VARCHAR(10)  NOT NULL DEFAULT 'FLAT' CHECK (position_direction IN ('FLAT', 'LONG', 'SHORT')),
    position_weight             NUMERIC(8,6) NOT NULL DEFAULT 0 CHECK (position_weight >= 0 AND position_weight <= 1),
    applied_signals             JSONB        NOT NULL DEFAULT '[]'::jsonb,
    cycle_id                    UUID,
    average_entry_price         NUMERIC(18,6),
    realized_pnl                NUMERIC(22,6) NOT NULL DEFAULT 0,
    updated_at                  TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_analysis_multi_ma_state PRIMARY KEY
    (stock_code, market_code, trading_venue, strategy_code, analysis_slot, ma_config_code, price_field_code)
);

CREATE TABLE IF NOT EXISTS analysis_multi_ma_signal
(
    signal_id                   BIGSERIAL    PRIMARY KEY,
    stock_code                  VARCHAR(20)  NOT NULL,
    market_code                 VARCHAR(30)  NOT NULL,
    trading_venue               VARCHAR(10)  NOT NULL,
    strategy_code               VARCHAR(30)  NOT NULL,
    analysis_slot               VARCHAR(10)  NOT NULL,
    ma_config_code              VARCHAR(100) NOT NULL,
    price_field_code            VARCHAR(30)  NOT NULL,
    signal_type                 VARCHAR(20)  NOT NULL CHECK (signal_type IN ('SIGNAL_1', 'SIGNAL_2', 'SIGNAL_3')),
    direction                   VARCHAR(10)  NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    signal_time                 TIMESTAMP(3) NOT NULL,
    signal_price                NUMERIC(18,2) NOT NULL,
    ma_short                    NUMERIC(18,6) NOT NULL,
    ma_mid                      NUMERIC(18,6) NOT NULL,
    ma_long                     NUMERIC(18,6) NOT NULL,
    short_slope                 NUMERIC(18,6),
    reason                      VARCHAR(500) NOT NULL,
    created_at                  TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_analysis_multi_ma_signal UNIQUE
    (stock_code, market_code, trading_venue, strategy_code, analysis_slot, signal_type, direction, signal_time)
);

CREATE TABLE IF NOT EXISTS analysis_multi_ma_trade
(
    trade_id                    BIGSERIAL    PRIMARY KEY,
    cycle_id                    UUID         NOT NULL,
    stock_code                  VARCHAR(20)  NOT NULL,
    market_code                 VARCHAR(30)  NOT NULL,
    trading_venue               VARCHAR(10)  NOT NULL,
    strategy_code               VARCHAR(30)  NOT NULL,
    analysis_slot               VARCHAR(10)  NOT NULL,
    signal_type                 VARCHAR(20),
    direction                   VARCHAR(10)  NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_time                  TIMESTAMP(3) NOT NULL,
    entry_price                 NUMERIC(18,2) NOT NULL,
    entry_weight                NUMERIC(8,6) NOT NULL CHECK (entry_weight > 0 AND entry_weight <= 1),
    cumulative_weight           NUMERIC(8,6) NOT NULL CHECK (cumulative_weight > 0 AND cumulative_weight <= 1),
    exit_time                   TIMESTAMP(3),
    exit_price                  NUMERIC(18,2),
    exit_type                   VARCHAR(20) CHECK (exit_type IN ('SIGNAL', 'SESSION_CLOSE')),
    exit_reason                 VARCHAR(30) CHECK (exit_reason IN ('SIGNAL_1', 'SIGNAL_2', 'SIGNAL_3', 'MULTIPLE_SIGNALS', 'SESSION_END')),
    realized_pnl                NUMERIC(22,6),
    realized_return             NUMERIC(18,10),
    cumulative_pnl              NUMERIC(22,6),
    cumulative_return           NUMERIC(18,10),
    detail_reason               VARCHAR(500) NOT NULL,
    created_at                  TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS exit_type VARCHAR(20);
ALTER TABLE analysis_multi_ma_trade ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(30);

CREATE TABLE IF NOT EXISTS analysis_multi_ma_summary
(
    trade_date                  DATE         NOT NULL,
    stock_code                  VARCHAR(20)  NOT NULL,
    market_code                 VARCHAR(30)  NOT NULL,
    trading_venue               VARCHAR(10)  NOT NULL,
    strategy_code               VARCHAR(30)  NOT NULL,
    analysis_slot               VARCHAR(10)  NOT NULL,
    ma_config_code              VARCHAR(100) NOT NULL,
    price_field_code            VARCHAR(30)  NOT NULL,
    initial_capital             NUMERIC(22,2) NOT NULL,
    cumulative_pnl              NUMERIC(22,6) NOT NULL DEFAULT 0,
    cumulative_return           NUMERIC(18,10) NOT NULL DEFAULT 0,
    trade_count                 INTEGER      NOT NULL DEFAULT 0,
    win_count                   INTEGER      NOT NULL DEFAULT 0,
    maximum_profit              NUMERIC(22,6),
    maximum_loss                NUMERIC(22,6),
    maximum_drawdown            NUMERIC(18,10) NOT NULL DEFAULT 0,
    signal_exit_count           INTEGER NOT NULL DEFAULT 0,
    session_close_exit_count    INTEGER NOT NULL DEFAULT 0,
    signal_exit_profit          NUMERIC(22,6) NOT NULL DEFAULT 0,
    session_close_exit_profit   NUMERIC(22,6) NOT NULL DEFAULT 0,
    current_position_direction  VARCHAR(10)  NOT NULL DEFAULT 'FLAT',
    current_position_weight     NUMERIC(8,6) NOT NULL DEFAULT 0,
    calculated_at               TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT pk_analysis_multi_ma_summary PRIMARY KEY
    (trade_date, stock_code, market_code, trading_venue, strategy_code, analysis_slot, ma_config_code, price_field_code)
);

ALTER TABLE analysis_multi_ma_state ADD COLUMN IF NOT EXISTS cycle_id UUID;
ALTER TABLE analysis_multi_ma_state ADD COLUMN IF NOT EXISTS average_entry_price NUMERIC(18,6);
ALTER TABLE analysis_multi_ma_state ADD COLUMN IF NOT EXISTS realized_pnl NUMERIC(22,6) NOT NULL DEFAULT 0;
ALTER TABLE analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS signal_exit_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS session_close_exit_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS signal_exit_profit NUMERIC(22,6) NOT NULL DEFAULT 0;
ALTER TABLE analysis_multi_ma_summary ADD COLUMN IF NOT EXISTS session_close_exit_profit NUMERIC(22,6) NOT NULL DEFAULT 0;
