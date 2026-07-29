SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS backfill_job
(
    job_id              BIGSERIAL PRIMARY KEY,
    job_type            VARCHAR(50)  NOT NULL,
    start_date          DATE         NOT NULL,
    end_date            DATE         NOT NULL,
    status              VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')),
    created_at          TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at          TIMESTAMP(3),
    completed_at        TIMESTAMP(3),
    failure_message     TEXT,

    CONSTRAINT ck_backfill_job_dates CHECK (start_date <= end_date)
);

COMMENT ON TABLE backfill_job IS '백필 실행 단위와 전체 진행 상태';
