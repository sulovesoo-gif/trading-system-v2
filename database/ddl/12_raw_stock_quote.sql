/******************************************************************************
 * File Name  : 12_raw_stock_quote.sql
 * Project    : Trading System V2
 * Description:
 *   주식현재가 시세 RAW 데이터
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

DROP TABLE IF EXISTS raw_stock_quote;

CREATE TABLE raw_stock_quote
(
    snapshot_time                TIMESTAMP(3) NOT NULL,
    collected_at                 TIMESTAMP(3) NOT NULL,
    data_source                  VARCHAR(30)  NOT NULL,
    market_code                  VARCHAR(30)  NOT NULL,
    collect_cycle                VARCHAR(10)  NOT NULL,
    stock_code                   VARCHAR(20)  NOT NULL,

    current_price                NUMERIC(18,2),
    previous_day_difference      NUMERIC(18,2),
    previous_day_difference_sign VARCHAR(1),
    change_rate                  NUMERIC(8,2),
    open_price                   NUMERIC(18,2),
    high_price                   NUMERIC(18,2),
    low_price                    NUMERIC(18,2),
    base_price                   NUMERIC(18,2),
    upper_limit_price            NUMERIC(18,2),
    lower_limit_price            NUMERIC(18,2),
    accumulated_volume           BIGINT,
    accumulated_amount           NUMERIC(20,2),
    weighted_average_price       NUMERIC(18,2),
    foreign_net_buy_volume       BIGINT,
    program_net_buy_volume       BIGINT,
    vi_classification_code       VARCHAR(10),
    trading_halt_yn              CHAR(1),

    raw_payload                  JSONB       NOT NULL,
    created_at                   TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_raw_stock_quote
        PRIMARY KEY
        (
            snapshot_time,
            data_source,
            market_code,
            collect_cycle,
            stock_code
        )
);

SELECT create_hypertable(
    'raw_stock_quote',
    'snapshot_time',
    if_not_exists => TRUE
);

CREATE INDEX idx_raw_stock_quote_code_time
    ON raw_stock_quote (stock_code, snapshot_time);

COMMENT ON TABLE raw_stock_quote IS '주식현재가 시세 RAW 데이터';
COMMENT ON COLUMN raw_stock_quote.snapshot_time IS '분석 기준 스냅샷 시간(KST)';
COMMENT ON COLUMN raw_stock_quote.collected_at IS '실제 API 수집 시간(KST)';
COMMENT ON COLUMN raw_stock_quote.data_source IS '데이터 제공처';
COMMENT ON COLUMN raw_stock_quote.market_code IS '시장코드';
COMMENT ON COLUMN raw_stock_quote.collect_cycle IS '수집주기';
COMMENT ON COLUMN raw_stock_quote.stock_code IS '종목코드';
COMMENT ON COLUMN raw_stock_quote.raw_payload IS '이 행에 대응하는 KIS API 원문 객체';
COMMENT ON COLUMN raw_stock_quote.created_at IS '레코드 생성 시간(KST)';
