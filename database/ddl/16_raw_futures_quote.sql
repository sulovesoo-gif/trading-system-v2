/******************************************************************************
 * File Name  : 16_raw_futures_quote.sql
 * Project    : Trading System V2
 * Description:
 *   선물옵션 시세 RAW 데이터
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

DROP TABLE IF EXISTS raw_futures_quote;

CREATE TABLE raw_futures_quote
(
    snapshot_time                TIMESTAMP(3) NOT NULL,
    collected_at                 TIMESTAMP(3) NOT NULL,
    data_source                  VARCHAR(30)  NOT NULL,
    market_code                  VARCHAR(30)  NOT NULL,
    trading_venue                VARCHAR(10)  NOT NULL CHECK (trading_venue IN ('KRX', 'NXT', 'INTEGRATED')),
    collect_cycle                VARCHAR(10)  NOT NULL,
    futures_code                 VARCHAR(20)  NOT NULL,

    futures_name                 VARCHAR(100),
    current_price                NUMERIC(18,2),
    previous_day_difference      NUMERIC(18,2),
    previous_day_difference_sign VARCHAR(1),
    previous_close_price         NUMERIC(18,2),
    change_rate                  NUMERIC(8,2),
    open_price                   NUMERIC(18,2),
    high_price                   NUMERIC(18,2),
    low_price                    NUMERIC(18,2),
    upper_limit_price            NUMERIC(18,2),
    lower_limit_price            NUMERIC(18,2),
    base_price                   NUMERIC(18,2),
    accumulated_volume           BIGINT,
    accumulated_amount           NUMERIC(20,2),
    open_interest                BIGINT,
    open_interest_change         BIGINT,
    basis                        NUMERIC(18,8),
    theoretical_price            NUMERIC(18,2),
    market_basis                 NUMERIC(18,8),
    expiration_date              DATE,
    days_to_expiration           INTEGER,

    raw_payload                  JSONB        NOT NULL,
    created_at                   TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_raw_futures_quote
        PRIMARY KEY
        (
            snapshot_time,
            data_source,
            market_code,
            trading_venue,
            collect_cycle,
            futures_code
        )
);

SELECT create_hypertable(
    'raw_futures_quote',
    'snapshot_time',
    if_not_exists => TRUE
);

CREATE INDEX idx_raw_futures_quote_code_time
    ON raw_futures_quote (futures_code, snapshot_time);

COMMENT ON TABLE raw_futures_quote IS '선물옵션 시세 RAW 데이터';
COMMENT ON COLUMN raw_futures_quote.snapshot_time IS '분석 기준 스냅샷 시간(KST)';
COMMENT ON COLUMN raw_futures_quote.collected_at IS '실제 API 수집 시간(KST)';
COMMENT ON COLUMN raw_futures_quote.data_source IS '데이터 제공처';
COMMENT ON COLUMN raw_futures_quote.market_code IS '시장코드';
COMMENT ON COLUMN raw_futures_quote.collect_cycle IS '수집주기';
COMMENT ON COLUMN raw_futures_quote.futures_code IS '선물 종목코드';
COMMENT ON COLUMN raw_futures_quote.futures_name IS '선물 종목명';
COMMENT ON COLUMN raw_futures_quote.raw_payload IS '이 행에 대응하는 KIS API 원문 객체';
COMMENT ON COLUMN raw_futures_quote.created_at IS '레코드 생성 시간(KST)';
