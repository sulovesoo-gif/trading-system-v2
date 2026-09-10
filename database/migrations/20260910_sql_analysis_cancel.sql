BEGIN;
ALTER TABLE sql_analysis_execution_history
    DROP CONSTRAINT sql_analysis_execution_history_status_check;
ALTER TABLE sql_analysis_execution_history
    ADD CONSTRAINT sql_analysis_execution_history_status_check
    CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED'));
COMMIT;
