/******************************************************************************
 * File Name  : 03_stock_master.sql
 * Project    : Trading System V2
 * Description:
 *   종목 마스터
 *
 * Rule:
 *   - 모든 시간 컬럼은 한국시간(KST, Asia/Seoul)을 기준으로 저장한다.
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

DROP TABLE IF EXISTS stock_master;

CREATE TABLE stock_master
(
    stock_code         VARCHAR(20)  NOT NULL,
    stock_name         VARCHAR(200) NOT NULL,
    market             VARCHAR(20)  NOT NULL,
    security_type      VARCHAR(30)  NOT NULL,

    collect_yn         CHAR(1)      NOT NULL DEFAULT 'Y',
    use_yn             CHAR(1)      NOT NULL DEFAULT 'Y',
    sort_order         INTEGER      NOT NULL DEFAULT 0,
    description        VARCHAR(500),

    created_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by         VARCHAR(50)  NOT NULL DEFAULT 'SYSTEM',
    updated_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by         VARCHAR(50)  NOT NULL DEFAULT 'SYSTEM',

    CONSTRAINT pk_stock_master
        PRIMARY KEY (stock_code),

    CONSTRAINT ck_stock_master_collect_yn
        CHECK (collect_yn IN ('Y', 'N')),

    CONSTRAINT ck_stock_master_use_yn
        CHECK (use_yn IN ('Y', 'N'))
);

COMMENT ON TABLE stock_master IS '종목 마스터. Collector 수집 대상의 유일한 기준';
COMMENT ON COLUMN stock_master.stock_code IS '종목코드';
COMMENT ON COLUMN stock_master.stock_name IS '종목명';
COMMENT ON COLUMN stock_master.market IS '시장(KOSPI,KOSDAQ,NXT,ETF,NYSE,NASDAQ 등)';
COMMENT ON COLUMN stock_master.security_type IS '종목유형(STOCK,ETF,ETN,FUTURE,OPTION 등)';
COMMENT ON COLUMN stock_master.collect_yn IS 'Collector 수집여부';
COMMENT ON COLUMN stock_master.use_yn IS '사용여부';
COMMENT ON COLUMN stock_master.sort_order IS '정렬순서';
COMMENT ON COLUMN stock_master.description IS '비고';
COMMENT ON COLUMN stock_master.created_at IS '등록일시(KST)';
COMMENT ON COLUMN stock_master.created_by IS '등록자';
COMMENT ON COLUMN stock_master.updated_at IS '수정일시(KST)';
COMMENT ON COLUMN stock_master.updated_by IS '수정자';
