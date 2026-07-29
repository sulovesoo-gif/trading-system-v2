/******************************************************************************
 * File Name  : 17_raw_futures_minute.sql
 * Project    : Trading System V2
 * Description:
 *   선물옵션 분봉조회 RAW 데이터
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

DROP TABLE IF EXISTS raw_futures_minute;

CREATE TABLE raw_futures_minute
(
    bar_time                    TIMESTAMP(3) NOT NULL,
    collected_at                TIMESTAMP(3) NOT NULL,
    data_source                 VARCHAR(30)  NOT NULL,
    market_code                 VARCHAR(30)  NOT NULL,
    trading_venue               VARCHAR(10)  NOT NULL CHECK (trading_venue IN ('KRX', 'NXT', 'INTEGRATED')),
    collect_cycle               VARCHAR(10)  NOT NULL,
    futures_code                VARCHAR(20)  NOT NULL,

    open_price                  NUMERIC(18,2),
    high_price                  NUMERIC(18,2),
    low_price                   NUMERIC(18,2),
    close_price                 NUMERIC(18,2),
    volume                      BIGINT,
    accumulated_amount          NUMERIC(20,2),

    raw_payload                 JSONB        NOT NULL,
    created_at                  TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_raw_futures_minute
        PRIMARY KEY
        (
            bar_time,
            data_source,
            market_code,
            trading_venue,
            collect_cycle,
            futures_code
        )
);

SELECT create_hypertable(
    'raw_futures_minute',
    'bar_time',
    if_not_exists => TRUE
);

CREATE INDEX idx_raw_futures_minute_code_time
    ON raw_futures_minute (futures_code, bar_time);

COMMENT ON TABLE raw_futures_minute IS '선물옵션 분봉조회 RAW 데이터';
COMMENT ON COLUMN raw_futures_minute.bar_time IS '분봉 기준 시간(KST)';
COMMENT ON COLUMN raw_futures_minute.collected_at IS '실제 API 수집 시간(KST)';
COMMENT ON COLUMN raw_futures_minute.data_source IS '데이터 제공처';
COMMENT ON COLUMN raw_futures_minute.market_code IS '시장코드';
COMMENT ON COLUMN raw_futures_minute.collect_cycle IS '수집주기';
COMMENT ON COLUMN raw_futures_minute.futures_code IS '선물 종목코드';
COMMENT ON COLUMN raw_futures_minute.raw_payload IS '이 행에 대응하는 KIS API 원문 객체';
COMMENT ON COLUMN raw_futures_minute.created_at IS '레코드 생성 시간(KST)';
