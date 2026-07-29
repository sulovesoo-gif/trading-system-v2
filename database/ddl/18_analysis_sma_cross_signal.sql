/******************************************************************************
 * File Name  : 18_analysis_sma_cross_signal.sql
 * Description: 완료 1분봉 기반 SMA5/SMA10 크로스 신호
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS analysis_sma_cross_signal
(
    signal_id                              BIGSERIAL     PRIMARY KEY,
    signal_time                            TIMESTAMP(3)  NOT NULL,
    stock_code                             VARCHAR(20)   NOT NULL,
    direction                              VARCHAR(10)   NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    status                                 VARCHAR(30)   NOT NULL CHECK (status IN ('INITIAL_CONFIRMED', 'CANDIDATE', 'CONFIRMED', 'REJECTED')),
    signal_price                           NUMERIC(18,2) NOT NULL,
    candle_open                            NUMERIC(18,2) NOT NULL,
    candle_close                           NUMERIC(18,2) NOT NULL,
    candle_direction                       VARCHAR(10)   NOT NULL CHECK (candle_direction IN ('UP', 'DOWN', 'FLAT')),
    direction_alignment                    VARCHAR(10)   NOT NULL CHECK (direction_alignment IN ('ALIGNED', 'OPPOSED', 'NEUTRAL')),
    sma5                                   NUMERIC(18,6) NOT NULL,
    sma10                                  NUMERIC(18,6) NOT NULL,
    previous_sma5                          NUMERIC(18,6) NOT NULL,
    previous_sma10                         NUMERIC(18,6) NOT NULL,
    previous_confirmed_signal_time         TIMESTAMP(3),
    previous_confirmed_signal_price        NUMERIC(18,2),
    maximum_up_change_since_previous       NUMERIC(12,8),
    maximum_down_change_since_previous     NUMERIC(12,8),
    maximum_absolute_change_since_previous NUMERIC(12,8),
    volatility_threshold_met               BOOLEAN       NOT NULL,
    threshold_break_direction              VARCHAR(10)   CHECK (threshold_break_direction IN ('UP', 'DOWN')),
    threshold_direction_alignment          VARCHAR(10)   CHECK (threshold_direction_alignment IN ('ALIGNED', 'OPPOSED')),
    rejection_reason                       VARCHAR(100),
    created_at                             TIMESTAMP(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status_updated_at                      TIMESTAMP(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_analysis_sma_cross_signal_event
        UNIQUE (stock_code, signal_time, status)
);

CREATE INDEX IF NOT EXISTS idx_analysis_sma_cross_signal_stock_time
    ON analysis_sma_cross_signal (stock_code, signal_time DESC);

COMMENT ON TABLE analysis_sma_cross_signal IS '완료된 SK하이닉스 1분봉 기반 SMA5/SMA10 크로스 분석 신호';
COMMENT ON COLUMN analysis_sma_cross_signal.signal_price IS '신호 발생 완료 1분봉의 종가';
COMMENT ON COLUMN analysis_sma_cross_signal.maximum_down_change_since_previous IS '직전 확정 타점 이후 종가 기준 최저 변동률(음수)';
COMMENT ON COLUMN analysis_sma_cross_signal.threshold_break_direction IS '후보를 확정한 완료 종가의 1% 경계 돌파 방향';
