DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM minute_ma_policy_historical_trade)
     OR EXISTS (SELECT 1 FROM minute_ma_policy_historical_run) THEN
    RAISE EXCEPTION 'Historical research assets exist; rollback is blocked';
  END IF;
END $$;
DROP VIEW IF EXISTS vw_minute_ma_v1_current_historical_run;
DROP TABLE IF EXISTS minute_ma_policy_historical_trade;
DROP TABLE IF EXISTS minute_ma_policy_historical_run;
