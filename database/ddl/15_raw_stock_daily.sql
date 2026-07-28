/******************************************************************************
 * File Name  : 15_raw_stock_daily.sql
 * Project    : Trading System V2
 * Description:
 *   국내주식기간별시세 RAW 데이터
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

DROP TABLE IF EXISTS raw_stock_daily;

CREATE TABLE raw_stock_daily
(
    trade_date                  DATE         NOT NULL,
    collected_at                TIMESTAMP(3) NOT NULL,
    data_source                 VARCHAR(30)  NOT NULL,
    market_code                 VARCHAR(30)  NOT NULL,
    collect_cycle               VARCHAR(10)  NOT NULL,
    stock_code                  VARCHAR(20)  NOT NULL,

    open_price                  NUMERIC(18,2),
    high_price                  NUMERIC(18,2),
    low_price                   NUMERIC(18,2),
    close_price                 NUMERIC(18,2),
    volume                      BIGINT,
    amount                      NUMERIC(20,2),
    previous_day_difference     NUMERIC(18,2),
    previous_day_difference_sign VARCHAR(1),
    adjusted_yn                 CHAR(1),
    split_rate                  NUMERIC(20,8),

    raw_payload                 JSONB        NOT NULL,
    created_at                  TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_raw_stock_daily
        PRIMARY KEY
        (
            trade_date,
            data_source,
            market_code,
            collect_cycle,
            stock_code
        )
);

SELECT create_hypertable(
    'raw_stock_daily',
    'trade_date',
    if_not_exists => TRUE
);

CREATE INDEX idx_raw_stock_daily_code_date
    ON raw_stock_daily (stock_code, trade_date);

COMMENT ON TABLE raw_stock_daily IS '국내주식기간별시세 RAW 데이터';
COMMENT ON COLUMN raw_stock_daily.trade_date IS '거래일(KST)';
COMMENT ON COLUMN raw_stock_daily.collected_at IS '실제 API 수집 시간(KST)';
COMMENT ON COLUMN raw_stock_daily.data_source IS '데이터 제공처';
COMMENT ON COLUMN raw_stock_daily.market_code IS '시장코드';
COMMENT ON COLUMN raw_stock_daily.collect_cycle IS '수집주기';
COMMENT ON COLUMN raw_stock_daily.stock_code IS '종목코드';
COMMENT ON COLUMN raw_stock_daily.raw_payload IS '이 행에 대응하는 KIS API 원문 객체';
COMMENT ON COLUMN raw_stock_daily.created_at IS '레코드 생성 시간(KST)';
