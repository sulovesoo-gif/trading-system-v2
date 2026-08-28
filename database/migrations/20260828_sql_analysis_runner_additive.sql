BEGIN;

CREATE TABLE IF NOT EXISTS sql_analysis_execution_history (
    execution_id uuid PRIMARY KEY,
    request_key varchar(128) NOT NULL UNIQUE,
    analysis_session_id uuid NOT NULL,
    research_title varchar(300),
    source_type varchar(16) NOT NULL CHECK (source_type IN ('UPLOAD', 'PASTE')),
    original_filename varchar(500),
    sql_text text NOT NULL,
    sql_sha256 char(64) NOT NULL,
    sql_size_bytes bigint NOT NULL CHECK (sql_size_bytes >= 0),
    status varchar(16) NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    queued_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    started_at timestamptz,
    finished_at timestamptz,
    duration_ms bigint,
    result_set_count integer NOT NULL DEFAULT 0,
    total_result_rows bigint NOT NULL DEFAULT 0,
    result_summary jsonb NOT NULL DEFAULT '[]'::jsonb,
    excel_filename varchar(500),
    excel_size_bytes bigint,
    error_sqlstate varchar(10),
    error_message text,
    error_statement_position integer,
    error_context text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS ix_sql_analysis_history_recent
    ON sql_analysis_execution_history (queued_at DESC);
CREATE INDEX IF NOT EXISTS ix_sql_analysis_history_session
    ON sql_analysis_execution_history (analysis_session_id, queued_at DESC);
CREATE INDEX IF NOT EXISTS ix_sql_analysis_history_status
    ON sql_analysis_execution_history (status, queued_at DESC);

COMMIT;
