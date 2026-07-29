SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS backfill_segment
(
    segment_id          BIGSERIAL PRIMARY KEY,
    job_id              BIGINT       NOT NULL REFERENCES backfill_job (job_id) ON DELETE CASCADE,
    instrument_code     VARCHAR(20)  NOT NULL,
    trading_venue       VARCHAR(10)  NOT NULL CHECK (trading_venue IN ('KRX', 'NXT', 'INTEGRATED')),
    trade_date          DATE         NOT NULL,
    page_sequence       INTEGER      NOT NULL DEFAULT 1 CHECK (page_sequence > 0),
    cursor_date         CHAR(8),
    cursor_time         CHAR(6),
    continuation_code   VARCHAR(10),
    status              VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
    attempt_count       INTEGER      NOT NULL DEFAULT 0,
    request_count       INTEGER      NOT NULL DEFAULT 0,
    returned_count      INTEGER      NOT NULL DEFAULT 0,
    inserted_count      INTEGER      NOT NULL DEFAULT 0,
    duplicate_count     INTEGER      NOT NULL DEFAULT 0,
    minimum_bar_time    TIMESTAMP(3),
    maximum_bar_time    TIMESTAMP(3),
    failure_message     TEXT,
    created_at          TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at          TIMESTAMP(3),
    completed_at        TIMESTAMP(3),

    CONSTRAINT uq_backfill_segment_page
        UNIQUE (job_id, instrument_code, trading_venue, trade_date, page_sequence)
);

CREATE INDEX IF NOT EXISTS idx_backfill_segment_job_status
    ON backfill_segment (job_id, status, trade_date, instrument_code);

COMMENT ON TABLE backfill_segment IS '백필 재개, 실패 구간, 페이지 커서 및 저장 건수 기록';
