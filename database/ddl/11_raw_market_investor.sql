/******************************************************************************
 * File Name  : 11_raw_market_investor.sql
 * Project    : Trading System V2
 * Description:
 *   시장별 투자자매매동향 RAW 데이터
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

DROP TABLE IF EXISTS raw_market_investor;

CREATE TABLE raw_market_investor
(
    snapshot_time                         TIMESTAMP(3) NOT NULL,
    collected_at                          TIMESTAMP(3) NOT NULL,
    data_source                           VARCHAR(30)  NOT NULL,
    market_code                           VARCHAR(30)  NOT NULL,
    collect_cycle                         VARCHAR(10)  NOT NULL,

    foreign_sell_volume                   BIGINT,
    foreign_buy_volume                    BIGINT,
    foreign_net_buy_volume                BIGINT,
    foreign_sell_amount                   NUMERIC(20,2),
    foreign_buy_amount                    NUMERIC(20,2),
    foreign_net_buy_amount                NUMERIC(20,2),

    individual_sell_volume                BIGINT,
    individual_buy_volume                 BIGINT,
    individual_net_buy_volume             BIGINT,
    individual_sell_amount                NUMERIC(20,2),
    individual_buy_amount                 NUMERIC(20,2),
    individual_net_buy_amount             NUMERIC(20,2),

    institution_sell_volume               BIGINT,
    institution_buy_volume                BIGINT,
    institution_net_buy_volume            BIGINT,
    institution_sell_amount               NUMERIC(20,2),
    institution_buy_amount                NUMERIC(20,2),
    institution_net_buy_amount            NUMERIC(20,2),

    financial_investment_sell_volume      BIGINT,
    financial_investment_buy_volume       BIGINT,
    financial_investment_net_buy_volume   BIGINT,
    financial_investment_sell_amount      NUMERIC(20,2),
    financial_investment_buy_amount       NUMERIC(20,2),
    financial_investment_net_buy_amount   NUMERIC(20,2),

    investment_trust_sell_volume          BIGINT,
    investment_trust_buy_volume           BIGINT,
    investment_trust_net_buy_volume       BIGINT,
    investment_trust_sell_amount          NUMERIC(20,2),
    investment_trust_buy_amount           NUMERIC(20,2),
    investment_trust_net_buy_amount       NUMERIC(20,2),

    private_fund_sell_volume              BIGINT,
    private_fund_buy_volume               BIGINT,
    private_fund_net_buy_volume           BIGINT,
    private_fund_sell_amount              NUMERIC(20,2),
    private_fund_buy_amount               NUMERIC(20,2),
    private_fund_net_buy_amount           NUMERIC(20,2),

    bank_sell_volume                      BIGINT,
    bank_buy_volume                       BIGINT,
    bank_net_buy_volume                   BIGINT,
    bank_sell_amount                      NUMERIC(20,2),
    bank_buy_amount                       NUMERIC(20,2),
    bank_net_buy_amount                   NUMERIC(20,2),

    insurance_sell_volume                 BIGINT,
    insurance_buy_volume                  BIGINT,
    insurance_net_buy_volume              BIGINT,
    insurance_sell_amount                 NUMERIC(20,2),
    insurance_buy_amount                  NUMERIC(20,2),
    insurance_net_buy_amount              NUMERIC(20,2),

    merchant_bank_sell_volume             BIGINT,
    merchant_bank_buy_volume              BIGINT,
    merchant_bank_net_buy_volume          BIGINT,
    merchant_bank_sell_amount             NUMERIC(20,2),
    merchant_bank_buy_amount              NUMERIC(20,2),
    merchant_bank_net_buy_amount          NUMERIC(20,2),

    fund_sell_volume                      BIGINT,
    fund_buy_volume                       BIGINT,
    fund_net_buy_volume                   BIGINT,
    fund_sell_amount                      NUMERIC(20,2),
    fund_buy_amount                       NUMERIC(20,2),
    fund_net_buy_amount                   NUMERIC(20,2),

    other_organization_sell_volume        BIGINT,
    other_organization_buy_volume         BIGINT,
    other_organization_net_buy_volume     BIGINT,
    other_organization_sell_amount        NUMERIC(20,2),
    other_organization_buy_amount         NUMERIC(20,2),
    other_organization_net_buy_amount     NUMERIC(20,2),

    other_corporation_sell_volume         BIGINT,
    other_corporation_buy_volume          BIGINT,
    other_corporation_net_buy_volume      BIGINT,
    other_corporation_sell_amount         NUMERIC(20,2),
    other_corporation_buy_amount          NUMERIC(20,2),
    other_corporation_net_buy_amount      NUMERIC(20,2),

    raw_payload                           JSONB       NOT NULL,
    created_at                            TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_raw_market_investor
        PRIMARY KEY
        (
            snapshot_time,
            data_source,
            market_code,
            collect_cycle
        )
);

SELECT create_hypertable(
    'raw_market_investor',
    'snapshot_time',
    if_not_exists => TRUE
);

CREATE INDEX idx_raw_market_investor_snapshot_time
    ON raw_market_investor (snapshot_time);

COMMENT ON TABLE raw_market_investor IS '시장별 투자자매매동향 RAW 데이터';
COMMENT ON COLUMN raw_market_investor.snapshot_time IS '분석 기준 스냅샷 시간(KST)';
COMMENT ON COLUMN raw_market_investor.collected_at IS '실제 API 수집 시간(KST)';
COMMENT ON COLUMN raw_market_investor.data_source IS '데이터 제공처';
COMMENT ON COLUMN raw_market_investor.market_code IS '시장코드';
COMMENT ON COLUMN raw_market_investor.collect_cycle IS '수집주기';
COMMENT ON COLUMN raw_market_investor.raw_payload IS '이 행에 대응하는 KIS API 원문 객체';
COMMENT ON COLUMN raw_market_investor.created_at IS '레코드 생성 시간(KST)';
