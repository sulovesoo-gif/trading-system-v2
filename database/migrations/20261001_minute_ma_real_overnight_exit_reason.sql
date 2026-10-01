-- V1.8.1: only expand the two existing PAPER exit-reason constraints.
-- No row updates, historical reclassification, capital or RAW changes.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';
LOCK TABLE minute_ma_real_paper_trade IN ACCESS EXCLUSIVE MODE;
DO $$
DECLARE
    item record;
    before_counts jsonb;
    after_counts jsonb;
BEGIN
    IF (SELECT count(*) FROM pg_constraint
        WHERE conrelid='minute_ma_real_paper_trade'::regclass AND contype='c'
          AND conname IN ('minute_ma_real_paper_trade_exit_reason_check',
                          'minute_ma_real_paper_trade_exit_reason_check1')) <> 2 THEN
        RAISE EXCEPTION 'Expected both existing PAPER exit-reason CHECKs';
    END IF;
    FOR item IN SELECT conname, pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE conrelid='minute_ma_real_paper_trade'::regclass
          AND conname IN ('minute_ma_real_paper_trade_exit_reason_check',
                          'minute_ma_real_paper_trade_exit_reason_check1')
    LOOP
        RAISE NOTICE 'Before: % %', item.conname, item.definition;
        IF item.definition <> 'CHECK (((exit_reason IS NULL) OR ((exit_reason)::text = ''NORMAL_EXIT''::text)))' THEN
            RAISE EXCEPTION 'Unexpected existing CHECK definition: %', item.definition;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM minute_ma_real_paper_trade
        WHERE exit_reason IS NOT NULL AND exit_reason NOT IN
          ('NORMAL_EXIT','OVERNIGHT_UP_0903','OVERNIGHT_DOWNFLAT_0910')) THEN
        RAISE EXCEPTION 'Existing rows incompatible with V1.8.1';
    END IF;
    SELECT jsonb_agg(to_jsonb(s) ORDER BY lifecycle_status, exit_reason)
      INTO before_counts FROM (
        SELECT lifecycle_status,exit_reason,count(*) AS row_count
        FROM minute_ma_real_paper_trade GROUP BY 1,2) s;
    RAISE NOTICE 'Before counts: %', before_counts;
    ALTER TABLE minute_ma_real_paper_trade
      DROP CONSTRAINT minute_ma_real_paper_trade_exit_reason_check,
      DROP CONSTRAINT minute_ma_real_paper_trade_exit_reason_check1,
      ADD CONSTRAINT minute_ma_real_paper_trade_exit_reason_check
        CHECK (exit_reason IS NULL OR exit_reason IN
          ('NORMAL_EXIT','OVERNIGHT_UP_0903','OVERNIGHT_DOWNFLAT_0910')),
      ADD CONSTRAINT minute_ma_real_paper_trade_exit_reason_check1
        CHECK (exit_reason IS NULL OR exit_reason IN
          ('NORMAL_EXIT','OVERNIGHT_UP_0903','OVERNIGHT_DOWNFLAT_0910'));
    SELECT jsonb_agg(to_jsonb(s) ORDER BY lifecycle_status, exit_reason)
      INTO after_counts FROM (
        SELECT lifecycle_status,exit_reason,count(*) AS row_count
        FROM minute_ma_real_paper_trade GROUP BY 1,2) s;
    IF before_counts IS DISTINCT FROM after_counts THEN
        RAISE EXCEPTION 'PAPER preservation verification failed';
    END IF;
    RAISE NOTICE 'After counts: %', after_counts;
END $$;
COMMIT;
