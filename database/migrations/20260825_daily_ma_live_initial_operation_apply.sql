-- User-approved initial LIVE operation + V0.4 capital epoch transition.
BEGIN;
CREATE TABLE IF NOT EXISTS daily_strategy_live_initial_capital_approval (
 selection_batch_id VARCHAR(64) NOT NULL REFERENCES daily_strategy_selection_batch(selection_batch_id),
 strategy_id VARCHAR(20) NOT NULL REFERENCES daily_strategy_master(strategy_id),
 approved_initial_capital NUMERIC(18,2) NOT NULL CHECK(approved_initial_capital>0),
 approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 approved_by VARCHAR(64) NOT NULL, approval_reference TEXT NOT NULL,
 PRIMARY KEY(selection_batch_id,strategy_id)
);
DO $$ DECLARE n bigint; BEGIN
 SELECT count(*) INTO n FROM daily_strategy_selection_batch WHERE selection_batch_id='DAILY_MA_SEL_20260824_V1' AND status='APPROVED';
 IF n<>1 THEN RAISE EXCEPTION 'approved selection batch required'; END IF;
 SELECT count(*) INTO n FROM daily_strategy_selection_snapshot WHERE selection_batch_id='DAILY_MA_SEL_20260824_V1' AND decision_status='SELECTED';
 IF n<>346 THEN RAISE EXCEPTION 'expected 346 selected strategies, got %',n; END IF;
 SELECT count(*) INTO n FROM daily_strategy_selection_snapshot s JOIN daily_strategy_operation o ON o.strategy_id=s.strategy_id AND o.effective_to IS NULL
  WHERE s.selection_batch_id='DAILY_MA_SEL_20260824_V1' AND s.decision_status='SELECTED' AND o.operation_status='PAPER';
 IF n<>346 THEN RAISE EXCEPTION 'expected 346 current PAPER operations, got %',n; END IF;
 SELECT count(*) INTO n FROM daily_strategy_compound_capital;
 IF n<>0 THEN RAISE EXCEPTION 'initial epoch apply requires no prior capital rows, got %',n; END IF;
END $$;
INSERT INTO daily_strategy_live_initial_capital_approval(selection_batch_id,strategy_id,approved_initial_capital,approved_by,approval_reference)
SELECT s.selection_batch_id,s.strategy_id,s.recommended_amount,'USER_APPROVED_20260825','DAILY_MA_SEL_20260824_V1 initial LIVE capital approval'
FROM daily_strategy_selection_snapshot s WHERE s.selection_batch_id='DAILY_MA_SEL_20260824_V1' AND s.decision_status='SELECTED';
UPDATE daily_strategy_operation o SET effective_to=CURRENT_TIMESTAMP,change_reason='SEL_LIVE_INITIAL',
 changed_by='USER_APPROVED_20260825',memo='superseded by initial approved LIVE operation'
FROM daily_strategy_selection_snapshot s
WHERE s.selection_batch_id='DAILY_MA_SEL_20260824_V1' AND s.decision_status='SELECTED'
 AND o.strategy_id=s.strategy_id AND o.effective_to IS NULL AND o.operation_status='PAPER';
WITH inserted AS (
 INSERT INTO daily_strategy_operation(strategy_id,operation_status,allocated_amount,capital_epoch_no,effective_from,change_reason,changed_by,memo)
 SELECT strategy_id,'LIVE',recommended_amount,1,CURRENT_TIMESTAMP,'SEL_LIVE_INITIAL','USER_APPROVED_20260825','initial LIVE operation; actual send remains locked'
 FROM daily_strategy_selection_snapshot WHERE selection_batch_id='DAILY_MA_SEL_20260824_V1' AND decision_status='SELECTED'
 RETURNING operation_id,strategy_id,allocated_amount,capital_epoch_no
)
INSERT INTO daily_strategy_compound_capital(strategy_id,capital_epoch_no,source_operation_id,epoch_initial_capital,strategy_compound_capital,cumulative_net_realized_pnl)
SELECT strategy_id,capital_epoch_no,operation_id,allocated_amount,allocated_amount,0 FROM inserted;
DO $$ DECLARE l bigint;p bigint;t bigint;c bigint; BEGIN
 SELECT count(*) FILTER(WHERE o.operation_status='LIVE'),count(*) FILTER(WHERE o.operation_status='PAPER'),count(*) INTO l,p,t
 FROM daily_strategy_operation o JOIN daily_strategy_master m USING(strategy_id) WHERE o.effective_to IS NULL AND m.strategy_role='CANONICAL' AND m.is_enabled='Y';
 IF l<>346 OR p<>2054 OR t<>2400 THEN RAISE EXCEPTION 'canonical current operations mismatch LIVE %, PAPER %, total %',l,p,t; END IF;
 SELECT count(*) INTO c FROM daily_strategy_compound_capital WHERE capital_epoch_no=1 AND strategy_compound_capital=epoch_initial_capital AND cumulative_net_realized_pnl=0;
 IF c<>346 THEN RAISE EXCEPTION 'initial capital epoch mismatch %',c; END IF;
END $$;
COMMIT;
