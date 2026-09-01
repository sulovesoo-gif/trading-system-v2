BEGIN;
DO $$ BEGIN
  IF to_regclass('public.minute_ma_policy_paper_pending_exit') IS NOT NULL
     AND EXISTS (SELECT 1 FROM minute_ma_policy_paper_pending_exit) THEN
    RAISE EXCEPTION 'Minute V1 PAPER pending-exit rows exist; guarded rollback refused';
  END IF;
END $$;
DROP TABLE IF EXISTS minute_ma_policy_paper_pending_exit;
COMMIT;
