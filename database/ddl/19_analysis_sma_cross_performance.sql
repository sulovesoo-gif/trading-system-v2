/******************************************************************************
 * File Name  : 19_analysis_sma_cross_performance.sql
 * Description: SMA 크로스 신호의 이후 성과
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS analysis_sma_cross_performance
(
    signal_id                                  BIGINT        PRIMARY KEY
        REFERENCES analysis_sma_cross_signal (signal_id),
    return_after_1m                            NUMERIC(12,8),
    return_after_3m                            NUMERIC(12,8),
    return_after_5m                            NUMERIC(12,8),
    return_after_10m                           NUMERIC(12,8),
    maximum_up_return_until_next_confirmed     NUMERIC(12,8),
    maximum_down_return_until_next_confirmed   NUMERIC(12,8),
    performance_end_time                       TIMESTAMP(3),
    performance_end_reason                     VARCHAR(30)
        CHECK (performance_end_reason IN ('NEXT_CONFIRMED_SIGNAL', 'MARKET_CLOSE')),
    last_evaluated_bar_time                    TIMESTAMP(3),
    created_at                                 TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                                 TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE analysis_sma_cross_performance IS 'SMA 크로스 후보 및 확정 신호의 완료 1분봉 성과';
COMMENT ON COLUMN analysis_sma_cross_performance.maximum_down_return_until_next_confirmed IS '신호가의 이후 최저 수익률(음수)';
