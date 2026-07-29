/******************************************************************************
 * File Name  : 21_analysis_sma_cross_related_bar.sql
 * Description: SMA 신호 시점 대응 상품의 가장 가까운 완료 1분봉 상태
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS analysis_sma_cross_related_bar
(
    signal_id       BIGINT        NOT NULL REFERENCES analysis_sma_cross_signal (signal_id),
    stock_code      VARCHAR(20)   NOT NULL,
    trading_venue   VARCHAR(10)   NOT NULL CHECK (trading_venue IN ('KRX', 'NXT', 'INTEGRATED')),
    bar_time        TIMESTAMP(3)  NOT NULL,
    open_price      NUMERIC(18,2) NOT NULL,
    high_price      NUMERIC(18,2) NOT NULL,
    low_price       NUMERIC(18,2) NOT NULL,
    close_price     NUMERIC(18,2) NOT NULL,
    volume          BIGINT,
    accumulated_amount NUMERIC(20,2),
    created_at      TIMESTAMP(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (signal_id, stock_code, trading_venue)
);

COMMENT ON TABLE analysis_sma_cross_related_bar IS 'SMA 타점 시점의 대응 레버리지·인버스 완료 1분봉 상태';
