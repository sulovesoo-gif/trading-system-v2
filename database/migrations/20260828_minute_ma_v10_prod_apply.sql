-- User-approved V1 selection/operation plan.  Do not run without a separate
-- production APPLY approval.  This file intentionally leaves SEND locked.
BEGIN;

DO $$
DECLARE
  candidate_count integer;
  candidate_capital numeric;
  legacy_live_count integer;
  open_count integer;
  send_value char(1);
BEGIN
  SELECT count(*),sum(proposed_initial_capital)
    INTO candidate_count,candidate_capital FROM minute_ma_v1_candidate_plan;
  SELECT count(*) INTO legacy_live_count FROM minute_ma_operation
   WHERE effective_to IS NULL AND operation_status='LIVE';
  SELECT count(*) INTO open_count FROM minute_ma_live_trade WHERE trade_status='OPEN';
  SELECT send_enabled INTO send_value FROM minute_ma_send_profile
   WHERE profile_code='MINUTE_MA_LIVE_SEND';
  IF candidate_count<>20 OR candidate_capital<>3200000 OR legacy_live_count<>2
     OR open_count<>0 OR send_value IS DISTINCT FROM 'N' THEN
    RAISE EXCEPTION 'V1 apply guard failed: candidates %, capital %, legacy LIVE %, OPEN %, SEND %',
      candidate_count,candidate_capital,legacy_live_count,open_count,send_value;
  END IF;
END $$;

INSERT INTO minute_ma_selection_batch(
  selection_batch_id,selected_at,evaluation_from,evaluation_to,
  metric_contract_version,status,source_artifacts,description,created_by,
  selection_scope,policy_version,selection_purpose)
VALUES(
  'MINUTE_MA_V1_SEL_20260828_V1',TIMESTAMP '2026-08-28 00:00:00',
  DATE '2026-05-27',DATE '2026-08-27','MINUTE_MA_OPERATION_V1.0','DRAFT',
  jsonb_build_array('Trading_System_V2_분봉_MA_운영전략_확정기준서_V1.0_20260828.docx'),
  'User-approved Minute MA V1.0 policy-path operating candidates',
  'USER_APPROVAL','MINUTE_MA_V1_OPERATION','V1.0','OPERATION')
ON CONFLICT(selection_batch_id) DO NOTHING;

INSERT INTO minute_ma_selection_snapshot(
  selection_batch_id,minute_path_id,minute_policy_path_id,source_daily_strategy_id,
  decision_status,robustness_yn,recommended_amount,approved_amount,reason_codes,source_row)
SELECT 'MINUTE_MA_V1_SEL_20260828_V1',pp.minute_path_id,pp.minute_policy_path_id,
       c.source_daily_strategy_id,'SELECTED','N',c.proposed_initial_capital,
       c.proposed_initial_capital,ARRAY['MANUAL_V1_OPERATION_APPROVAL'],
       jsonb_build_object('policy_code',c.policy_code,'candidate_class',c.candidate_class,
                          'source_reference',c.source_reference)
  FROM minute_ma_v1_candidate_plan c
  JOIN minute_ma_strategy_master s
    ON s.source_daily_strategy_id=c.source_daily_strategy_id
  JOIN minute_ma_path p
    ON p.minute_strategy_id=s.minute_strategy_id AND p.data_axis='KRX_CONTINUOUS'
  JOIN minute_ma_policy_path pp
    ON pp.minute_path_id=p.minute_path_id AND pp.policy_code=c.policy_code
ON CONFLICT(selection_batch_id,minute_path_id) DO NOTHING;

DO $$
DECLARE n integer; wrong_join integer;
BEGIN
  SELECT count(*) INTO n FROM minute_ma_selection_snapshot
   WHERE selection_batch_id='MINUTE_MA_V1_SEL_20260828_V1';
  SELECT count(*) INTO wrong_join
    FROM minute_ma_selection_snapshot s
    JOIN minute_ma_policy_path pp USING(minute_policy_path_id)
    JOIN minute_ma_strategy_master m ON m.source_daily_strategy_id=s.source_daily_strategy_id
   WHERE s.selection_batch_id='MINUTE_MA_V1_SEL_20260828_V1'
     AND (pp.minute_path_id<>s.minute_path_id OR pp.policy_code<>
          CASE m.direction WHEN 'LONG' THEN 'MINUTE_MA_V1_LONG' ELSE 'MINUTE_MA_V1_SHORT' END);
  IF n<>20 OR wrong_join<>0 THEN
    RAISE EXCEPTION 'V1 selection verification failed: rows %, wrong joins %',n,wrong_join;
  END IF;
END $$;

UPDATE minute_ma_selection_batch SET status='APPROVED'
 WHERE selection_batch_id='MINUTE_MA_V1_SEL_20260828_V1' AND status='DRAFT';

-- Explicit PAPER current state for all V1 paths.  This does not touch legacy
-- minute_ma_operation.
INSERT INTO minute_ma_policy_operation(
  minute_policy_path_id,operation_status,allocated_amount,capital_epoch_no,
  effective_from,change_reason,audit_reference)
SELECT minute_policy_path_id,'PAPER',0,0,TIMESTAMP '2026-08-28 00:00:00',
       'MANUAL','MINUTE_MA_V1_BASELINE_PAPER'
  FROM minute_ma_policy_path
ON CONFLICT(minute_policy_path_id) WHERE effective_to IS NULL DO NOTHING;

UPDATE minute_ma_policy_operation o SET effective_to=CURRENT_TIMESTAMP
 WHERE o.effective_to IS NULL AND o.operation_status='PAPER'
   AND o.minute_policy_path_id IN (
     SELECT minute_policy_path_id FROM minute_ma_selection_snapshot
      WHERE selection_batch_id='MINUTE_MA_V1_SEL_20260828_V1'
        AND decision_status='SELECTED');

INSERT INTO minute_ma_policy_operation(
  minute_policy_path_id,operation_status,allocated_amount,capital_epoch_no,
  effective_from,change_reason,audit_reference)
SELECT s.minute_policy_path_id,'LIVE',s.approved_amount,1,CURRENT_TIMESTAMP,
       'MANUAL','MINUTE_MA_V1_SEL_20260828_V1'
  FROM minute_ma_selection_snapshot s
 WHERE s.selection_batch_id='MINUTE_MA_V1_SEL_20260828_V1'
   AND s.decision_status='SELECTED'
ON CONFLICT(minute_policy_path_id) WHERE effective_to IS NULL DO NOTHING;

INSERT INTO minute_ma_policy_compound_capital(
  minute_policy_path_id,capital_epoch_no,source_policy_operation_id,
  epoch_initial_capital,strategy_compound_capital,cumulative_net_realized_pnl)
SELECT o.minute_policy_path_id,o.capital_epoch_no,o.minute_policy_operation_id,
       o.allocated_amount,o.allocated_amount,0
  FROM minute_ma_policy_operation o
 WHERE o.effective_to IS NULL AND o.operation_status='LIVE'
   AND o.audit_reference='MINUTE_MA_V1_SEL_20260828_V1'
ON CONFLICT(minute_policy_path_id,capital_epoch_no) DO NOTHING;

-- Retire exactly the two historical Minute LIVE tests and restore those
-- legacy paths to PAPER.  Their operations/capital epochs remain immutable.
UPDATE minute_ma_operation o SET effective_to=CURRENT_TIMESTAMP
 WHERE o.effective_to IS NULL AND o.operation_status='LIVE'
   AND o.minute_path_id IN (
     SELECT p.minute_path_id FROM minute_ma_path p
     JOIN minute_ma_strategy_master s USING(minute_strategy_id)
     WHERE (s.source_daily_strategy_id='DS001283' AND p.data_axis='INTEGRATED_CONTINUOUS')
        OR (s.source_daily_strategy_id='DS002277' AND p.data_axis='KRX_CONTINUOUS'));

INSERT INTO minute_ma_operation(
  minute_path_id,operation_status,allocated_amount,capital_epoch_no,
  effective_from,change_reason,audit_reference)
SELECT p.minute_path_id,'PAPER',0,0,CURRENT_TIMESTAMP,'MANUAL',
       'MINUTE_MA_V1_TEST_LIVE_RETURN_TO_PAPER'
  FROM minute_ma_path p JOIN minute_ma_strategy_master s USING(minute_strategy_id)
 WHERE (s.source_daily_strategy_id='DS001283' AND p.data_axis='INTEGRATED_CONTINUOUS')
    OR (s.source_daily_strategy_id='DS002277' AND p.data_axis='KRX_CONTINUOUS')
ON CONFLICT(minute_path_id) WHERE effective_to IS NULL DO NOTHING;

DO $$
DECLARE v1_live integer; v1_paper integer; legacy_live integer; capital_count integer;
        capital_sum numeric; wrong_live integer; selection_count integer;
        legacy_test_paper integer; legacy_test_capital integer;
BEGIN
  SELECT count(*) FILTER(WHERE operation_status='LIVE'),
         count(*) FILTER(WHERE operation_status='PAPER')
    INTO v1_live,v1_paper FROM minute_ma_policy_operation WHERE effective_to IS NULL;
  SELECT count(*) INTO legacy_live FROM minute_ma_operation
   WHERE effective_to IS NULL AND operation_status='LIVE';
  SELECT count(*) INTO legacy_test_paper
    FROM minute_ma_operation o JOIN minute_ma_path p USING(minute_path_id)
    JOIN minute_ma_strategy_master m USING(minute_strategy_id)
   WHERE o.effective_to IS NULL AND o.operation_status='PAPER'
     AND ((m.source_daily_strategy_id='DS001283' AND p.data_axis='INTEGRATED_CONTINUOUS')
       OR (m.source_daily_strategy_id='DS002277' AND p.data_axis='KRX_CONTINUOUS'));
  SELECT count(*) INTO legacy_test_capital
    FROM minute_ma_compound_capital c JOIN minute_ma_path p USING(minute_path_id)
    JOIN minute_ma_strategy_master m USING(minute_strategy_id)
   WHERE (m.source_daily_strategy_id='DS001283' AND p.data_axis='INTEGRATED_CONTINUOUS')
      OR (m.source_daily_strategy_id='DS002277' AND p.data_axis='KRX_CONTINUOUS');
  SELECT count(*),sum(epoch_initial_capital) INTO capital_count,capital_sum
    FROM minute_ma_policy_compound_capital;
  SELECT count(*) INTO wrong_live FROM minute_ma_policy_operation o
    LEFT JOIN minute_ma_selection_snapshot s
      ON s.minute_policy_path_id=o.minute_policy_path_id
     AND s.selection_batch_id='MINUTE_MA_V1_SEL_20260828_V1'
   WHERE o.effective_to IS NULL AND o.operation_status='LIVE'
     AND s.minute_policy_path_id IS NULL;
  SELECT count(*) INTO selection_count FROM minute_ma_selection_snapshot
   WHERE selection_batch_id='MINUTE_MA_V1_SEL_20260828_V1';
  IF v1_live<>20 OR v1_paper<>2380 OR legacy_live<>0 OR legacy_test_paper<>2
     OR legacy_test_capital<>2 OR capital_count<>20
     OR capital_sum<>3200000 OR wrong_live<>0 OR selection_count<>20 THEN
    RAISE EXCEPTION 'V1 final guard failed: LIVE %, PAPER %, legacy LIVE %, test PAPER/capital %/%, capital %/% wrong % selection %',
      v1_live,v1_paper,legacy_live,legacy_test_paper,legacy_test_capital,
      capital_count,capital_sum,wrong_live,selection_count;
  END IF;
END $$;

COMMIT;
