/******************************************************************************
 * File Name  : 10_raw_program.sql
 * Project    : Trading System V2
 * Description:
 *   종목별 프로그램매매 RAW 데이터
 *
 * Source:
 *   한국투자증권 Open API
 *   국내주식-044 프로그램매매 추이(종목)
 *
 * Rule:
 *   - 모든 시간 컬럼은 한국시간(KST, Asia/Seoul)을 기준으로 저장한다.
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

DROP TABLE IF EXISTS raw_program;

CREATE TABLE raw_program
(
    snapshot_time              TIMESTAMP(3) NOT NULL,
    collected_at               TIMESTAMP(3) NOT NULL,

    data_source                VARCHAR(30)  NOT NULL,
    market_code                VARCHAR(30)  NOT NULL,
    collect_cycle              VARCHAR(10)  NOT NULL,

    stock_code                 VARCHAR(20)  NOT NULL,

    current_price              BIGINT,
    previous_day_difference    BIGINT,
    previous_day_difference_sign VARCHAR(1),
    change_rate                NUMERIC(8,2),

    accumulated_volume         BIGINT,

    sell_volume                BIGINT,
    buy_volume                 BIGINT,
    net_buy_volume             BIGINT,

    sell_amount                BIGINT,
    buy_amount                 BIGINT,
    net_buy_amount             BIGINT,

    net_buy_volume_change      BIGINT,
    net_buy_amount_change      BIGINT,

    raw_payload                JSONB       NOT NULL,

    created_at                 TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_raw_program
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
    'raw_program',
    'snapshot_time',
    if_not_exists => TRUE
);

CREATE INDEX idx_raw_program_stock_time
    ON raw_program
    (
        stock_code,
        snapshot_time
    );

CREATE INDEX idx_raw_program_snapshot_time
    ON raw_program
    (
        snapshot_time
    );

COMMENT ON TABLE raw_program IS '종목별 프로그램매매 RAW 데이터';

COMMENT ON COLUMN raw_program.snapshot_time IS '분석 기준 스냅샷 시간(API 기준, KST)';
COMMENT ON COLUMN raw_program.collected_at IS '실제 API 수집 시간(KST)';
COMMENT ON COLUMN raw_program.data_source IS '데이터 제공처(KIS)';
COMMENT ON COLUMN raw_program.market_code IS '시장코드(KOSPI/KOSDAQ 등)';
COMMENT ON COLUMN raw_program.collect_cycle IS '수집주기(1MIN)';
COMMENT ON COLUMN raw_program.stock_code IS '종목코드';
COMMENT ON COLUMN raw_program.current_price IS '현재가';
COMMENT ON COLUMN raw_program.previous_day_difference IS '전일 대비';
COMMENT ON COLUMN raw_program.previous_day_difference_sign IS '전일 대비 부호';
COMMENT ON COLUMN raw_program.change_rate IS '등락률';
COMMENT ON COLUMN raw_program.accumulated_volume IS '누적거래량';
COMMENT ON COLUMN raw_program.sell_volume IS '전체 합계 매도 거래량';
COMMENT ON COLUMN raw_program.buy_volume IS '전체 합계 매수 거래량';
COMMENT ON COLUMN raw_program.net_buy_volume IS '전체 합계 순매수 거래량';
COMMENT ON COLUMN raw_program.sell_amount IS '전체 합계 매도 거래대금';
COMMENT ON COLUMN raw_program.buy_amount IS '전체 합계 매수 거래대금';
COMMENT ON COLUMN raw_program.net_buy_amount IS '전체 합계 순매수 거래대금';
COMMENT ON COLUMN raw_program.net_buy_volume_change IS '전체 순매수 거래량 증감';
COMMENT ON COLUMN raw_program.net_buy_amount_change IS '전체 순매수 거래대금 증감';
COMMENT ON COLUMN raw_program.raw_payload IS '이 행에 대응하는 KIS API 원문 객체';
COMMENT ON COLUMN raw_program.created_at IS '레코드 생성 시간(KST)';
