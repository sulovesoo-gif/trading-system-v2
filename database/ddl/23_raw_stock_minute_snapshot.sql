/* 진행 중 1분봉의 5초 관찰 RAW. 완료봉 raw_stock_minute와 절대 혼용하지 않는다. */
SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS raw_stock_minute_snapshot
(
    snapshot_time               TIMESTAMP(3) NOT NULL,
    target_bar_time             TIMESTAMP(3) NOT NULL,
    collected_at                TIMESTAMP(3) NOT NULL,
    data_source                 VARCHAR(30)  NOT NULL,
    market_code                 VARCHAR(30)  NOT NULL,
    trading_venue               VARCHAR(10)  NOT NULL CHECK (trading_venue IN ('KRX', 'NXT', 'INTEGRATED')),
    collect_cycle               VARCHAR(10)  NOT NULL,
    stock_code                  VARCHAR(20)  NOT NULL,
    snapshot_second             SMALLINT     NOT NULL CHECK (snapshot_second IN (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55)),
    open_price                  NUMERIC(18,2),
    high_price                  NUMERIC(18,2),
    low_price                   NUMERIC(18,2),
    close_price                 NUMERIC(18,2),
    volume                      BIGINT,
    accumulated_amount          NUMERIC(20,2),
    raw_payload                 JSONB        NOT NULL,
    created_at                  TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_raw_stock_minute_snapshot PRIMARY KEY
    (
        snapshot_time, data_source, market_code, trading_venue, collect_cycle, stock_code
    )
);

SELECT create_hypertable('raw_stock_minute_snapshot', 'snapshot_time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_raw_stock_minute_snapshot_target
    ON raw_stock_minute_snapshot (stock_code, trading_venue, target_bar_time, snapshot_second);

COMMENT ON TABLE raw_stock_minute_snapshot IS '진행 중 1분봉을 5초 기준으로 관찰한 KIS 원문 RAW';
COMMENT ON COLUMN raw_stock_minute_snapshot.target_bar_time IS '관찰 대상 1분봉 시각(KST)';
COMMENT ON COLUMN raw_stock_minute_snapshot.snapshot_time IS '예정된 관찰 슬롯 시각(KST); 같은 슬롯 재시도는 기본키 중복으로 보존하지 않음';
COMMENT ON COLUMN raw_stock_minute_snapshot.snapshot_second IS '분 내 관찰 기준 초; 00초는 관찰 전용이며 완료봉 확정 근거가 아님';
