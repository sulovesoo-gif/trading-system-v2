SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS analysis_sma_cross_arm_state
(
    stock_code          VARCHAR(20)   PRIMARY KEY,
    armed_direction     VARCHAR(10)   NOT NULL CHECK (armed_direction IN ('LONG', 'SHORT')),
    ma_cross_time       TIMESTAMP(3)  NOT NULL,
    ma_cross_price      NUMERIC(18,2) NOT NULL,
    ma_cross_sma5       NUMERIC(18,6) NOT NULL,
    ma_cross_sma10      NUMERIC(18,6) NOT NULL,
    candidate_signal_id BIGINT        REFERENCES analysis_sma_cross_signal (signal_id),
    created_at          TIMESTAMP(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE analysis_sma_cross_arm_state IS '재시작 후 복구하는 종목별 SMA 교차 ARMED 상태';
