/******************************************************************************
 * File Name  : 20_analysis_signal_notification.sql
 * Description: SMA 크로스 이메일 알림 이력
 ******************************************************************************/

SET TIME ZONE 'Asia/Seoul';

CREATE TABLE IF NOT EXISTS analysis_signal_notification
(
    notification_id BIGSERIAL    PRIMARY KEY,
    signal_id       BIGINT       NOT NULL REFERENCES analysis_sma_cross_signal (signal_id),
    notification_type VARCHAR(30) NOT NULL CHECK (notification_type IN ('INITIAL', 'CANDIDATE', 'CONFIRMED')),
    delivery_status VARCHAR(20)  NOT NULL CHECK (delivery_status IN ('PENDING', 'SENT', 'FAILED')),
    attempt_count   INTEGER      NOT NULL DEFAULT 0,
    sent_at         TIMESTAMP(3),
    failure_message VARCHAR(500),
    created_at      TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_analysis_signal_notification
        UNIQUE (signal_id, notification_type)
);

COMMENT ON TABLE analysis_signal_notification IS '동일 신호·상태 이메일 중복 발송 방지 이력';
