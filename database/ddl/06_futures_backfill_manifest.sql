SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS futures_backfill_manifest
(
    manifest_id             BIGSERIAL PRIMARY KEY,
    instrument_key          VARCHAR(50)  NOT NULL,
    market_division_code    VARCHAR(10)  NOT NULL
        CHECK (market_division_code IN ('F', 'JF')),
    futures_code            VARCHAR(20)  NOT NULL,
    standard_code           VARCHAR(20),
    contract_name           VARCHAR(100),
    expiry_date             DATE,
    valid_from              DATE         NOT NULL,
    valid_to                DATE         NOT NULL,
    evidence_status         VARCHAR(40)  NOT NULL
        CHECK (
            evidence_status IN (
                'OFFICIAL_MASTER_VERIFIED',
                'API_VERIFIED_UNCONFIRMED'
            )
        ),
    evidence_reference      TEXT         NOT NULL,
    approved_at             TIMESTAMP(3),
    approval_note           TEXT,
    created_at              TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT ck_futures_backfill_manifest_dates
        CHECK (valid_from <= valid_to),
    CONSTRAINT ck_futures_backfill_manifest_unconfirmed
        CHECK (
            evidence_status <> 'API_VERIFIED_UNCONFIRMED'
            OR (
                standard_code IS NULL
                AND contract_name IS NULL
                AND expiry_date IS NULL
            )
        ),
    CONSTRAINT uq_futures_backfill_manifest
        UNIQUE (
            instrument_key,
            market_division_code,
            futures_code,
            valid_from,
            valid_to
        )
);

COMMENT ON TABLE futures_backfill_manifest IS '선물 과거 백필에 사용할 계약 코드와 근거 상태';
COMMENT ON COLUMN futures_backfill_manifest.valid_from IS '백필 수집 적용 시작일(계약 만기일 아님)';
COMMENT ON COLUMN futures_backfill_manifest.valid_to IS '백필 수집 적용 종료일(계약 만기일 아님)';
COMMENT ON COLUMN futures_backfill_manifest.evidence_status IS '공식 마스터 확인 또는 API 검증만 완료된 상태';

INSERT INTO futures_backfill_manifest
(
    instrument_key,
    market_division_code,
    futures_code,
    standard_code,
    contract_name,
    expiry_date,
    valid_from,
    valid_to,
    evidence_status,
    evidence_reference,
    approval_note
)
VALUES
(
    'KOSPI200_FUTURES',
    'F',
    'A01606',
    NULL,
    NULL,
    NULL,
    DATE '2026-05-27',
    DATE '2026-06-11',
    'API_VERIFIED_UNCONFIRMED',
    'FHKIF03020200: 2026-06-10 output2 102행 및 페이지 커서 연속성 확인. output1은 빈 객체.',
    '공식 마스터·표준코드·종목명·만기일 연결 전까지 미확정 계약으로 유지.'
),
(
    'KOSPI200_FUTURES',
    'F',
    'A01609',
    'KR4A01690002',
    'F 202609',
    NULL,
    DATE '2026-06-11',
    DATE '2026-07-28',
    'OFFICIAL_MASTER_VERIFIED',
    'fo_idx_code_mts.mst: A01609 / KR4A01690002 / F 202609 / KOSPI200. FHKIF03020200 과거 분봉 응답 확인.',
    '만기일은 공식 원본에서 직접 확인 전까지 NULL로 유지.'
)
ON CONFLICT ON CONSTRAINT uq_futures_backfill_manifest DO NOTHING;
