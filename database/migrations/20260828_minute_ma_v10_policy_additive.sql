-- Minute MA V1.0 versioned operating policy.  Additive; no operating writes.
BEGIN;

DO $$ BEGIN
  IF to_regclass('public.minute_ma_path') IS NULL
     OR to_regclass('public.minute_ma_paper_trade') IS NULL
     OR to_regclass('public.minute_ma_live_trade') IS NULL THEN
    RAISE EXCEPTION 'Minute MA V0.1/actual-send prerequisites are missing';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS minute_ma_operation_policy (
  policy_code VARCHAR(64) PRIMARY KEY,
  policy_version VARCHAR(32) NOT NULL,
  policy_scope VARCHAR(32) NOT NULL CHECK (policy_scope IN ('RESEARCH','OPERATION')),
  direction VARCHAR(8) NOT NULL CHECK (direction IN ('LONG','SHORT')),
  data_axis VARCHAR(32) NOT NULL CHECK (data_axis='KRX_CONTINUOUS'),
  paper_entry_start TIME NOT NULL,
  paper_entry_end TIME NOT NULL,
  live_entry_start TIME NOT NULL,
  live_entry_end TIME NOT NULL,
  holding_policy VARCHAR(48) NOT NULL CHECK (holding_policy='HOLD_TO_NORMAL_EXIT_OR_STOP'),
  stop_policy VARCHAR(48) NOT NULL CHECK (stop_policy IN ('UNDERLYING_1PCT','UNDERLYING_5PCT')),
  stop_percent NUMERIC(8,4) NOT NULL CHECK (stop_percent>0),
  stop_direction VARCHAR(8) NOT NULL CHECK (stop_direction IN ('ABOVE','BELOW')),
  is_enabled CHAR(1) NOT NULL DEFAULT 'Y' CHECK (is_enabled IN ('Y','N')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (paper_entry_end>=paper_entry_start AND live_entry_end>=live_entry_start)
);

INSERT INTO minute_ma_operation_policy(
  policy_code,policy_version,policy_scope,direction,data_axis,
  paper_entry_start,paper_entry_end,live_entry_start,live_entry_end,
  holding_policy,stop_policy,stop_percent,stop_direction)
VALUES
 ('MINUTE_MA_V1_SHORT','V1.0','OPERATION','SHORT','KRX_CONTINUOUS',
  TIME '09:00',TIME '09:59',TIME '09:00',TIME '09:29',
  'HOLD_TO_NORMAL_EXIT_OR_STOP','UNDERLYING_1PCT',1,'ABOVE'),
 ('MINUTE_MA_V1_LONG','V1.0','OPERATION','LONG','KRX_CONTINUOUS',
  TIME '14:00',TIME '15:18',TIME '15:00',TIME '15:18',
  'HOLD_TO_NORMAL_EXIT_OR_STOP','UNDERLYING_5PCT',5,'BELOW')
ON CONFLICT (policy_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS minute_ma_policy_path (
  minute_policy_path_id BIGSERIAL PRIMARY KEY,
  policy_path_key VARCHAR(300) NOT NULL UNIQUE,
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  policy_code VARCHAR(64) NOT NULL REFERENCES minute_ma_operation_policy(policy_code),
  is_enabled CHAR(1) NOT NULL DEFAULT 'Y' CHECK (is_enabled IN ('Y','N')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(minute_path_id,policy_code)
);

INSERT INTO minute_ma_policy_path(policy_path_key,minute_path_id,policy_code)
SELECT p.path_key||'|OPERATION_V1.0',p.minute_path_id,
       CASE s.direction WHEN 'LONG' THEN 'MINUTE_MA_V1_LONG' ELSE 'MINUTE_MA_V1_SHORT' END
  FROM minute_ma_path p
  JOIN minute_ma_strategy_master s USING(minute_strategy_id)
 WHERE p.data_axis='KRX_CONTINUOUS' AND p.is_enabled='Y' AND s.is_enabled='Y'
ON CONFLICT (minute_path_id,policy_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS minute_ma_policy_runtime_cursor (
  runtime_name VARCHAR(64) NOT NULL,
  policy_version VARCHAR(32) NOT NULL,
  signal_code VARCHAR(32) NOT NULL,
  last_source_bar_time TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(runtime_name,policy_version,signal_code)
);

CREATE TABLE IF NOT EXISTS minute_ma_policy_paper_event (
  minute_policy_paper_event_id BIGSERIAL PRIMARY KEY,
  minute_policy_path_id BIGINT NOT NULL REFERENCES minute_ma_policy_path(minute_policy_path_id),
  signal_event_key CHAR(64) NOT NULL,
  event_type VARCHAR(16) NOT NULL CHECK (event_type IN ('ENTRY','NORMAL_EXIT','STOP_EXIT')),
  source_bar_time TIMESTAMP NOT NULL,
  confirmed_at TIMESTAMP NOT NULL,
  proxy_bar_time TIMESTAMP,
  proxy_price NUMERIC,
  underlying_price NUMERIC,
  source_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(minute_policy_path_id,signal_event_key,event_type)
);

CREATE TABLE IF NOT EXISTS minute_ma_policy_paper_trade (
  minute_policy_paper_trade_id BIGSERIAL PRIMARY KEY,
  minute_policy_path_id BIGINT NOT NULL REFERENCES minute_ma_policy_path(minute_policy_path_id),
  entry_event_key CHAR(64) NOT NULL,
  trade_status VARCHAR(16) NOT NULL CHECK (trade_status IN ('OPEN','CLOSED','CANCELLED')),
  entry_signal_time TIMESTAMP NOT NULL,
  entry_execution_time TIMESTAMP NOT NULL,
  entry_price NUMERIC NOT NULL CHECK (entry_price>0),
  underlying_entry_reference_price NUMERIC NOT NULL CHECK (underlying_entry_reference_price>0),
  stop_threshold_price NUMERIC NOT NULL CHECK (stop_threshold_price>0),
  stop_policy VARCHAR(48) NOT NULL,
  exit_signal_time TIMESTAMP,
  exit_execution_time TIMESTAMP,
  exit_price NUMERIC CHECK (exit_price IS NULL OR exit_price>0),
  exit_reason VARCHAR(24) CHECK (exit_reason IS NULL OR exit_reason IN ('NORMAL_EXIT','STOP_EXIT')),
  stop_trigger_time TIMESTAMP,
  stop_trigger_underlying_close NUMERIC,
  gross_return_pct NUMERIC,
  net_return_pct NUMERIC,
  basis_capital NUMERIC NOT NULL CHECK (basis_capital>0),
  realized_pnl NUMERIC,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(minute_policy_path_id,entry_event_key),
  CHECK ((trade_status='OPEN' AND exit_execution_time IS NULL) OR
         (trade_status<>'OPEN' AND exit_execution_time IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_minute_ma_policy_paper_open
  ON minute_ma_policy_paper_trade(minute_policy_path_id,entry_execution_time)
  WHERE trade_status='OPEN';

CREATE TABLE IF NOT EXISTS minute_ma_policy_paper_capital (
  minute_policy_path_id BIGINT PRIMARY KEY REFERENCES minute_ma_policy_path(minute_policy_path_id),
  initial_capital NUMERIC NOT NULL DEFAULT 1000000 CHECK (initial_capital>0),
  current_capital NUMERIC NOT NULL DEFAULT 1000000,
  cumulative_realized_pnl NUMERIC NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (current_capital=initial_capital+cumulative_realized_pnl)
);
INSERT INTO minute_ma_policy_paper_capital(minute_policy_path_id)
SELECT minute_policy_path_id FROM minute_ma_policy_path ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS minute_ma_policy_paper_settlement (
  minute_policy_paper_trade_id BIGINT PRIMARY KEY
    REFERENCES minute_ma_policy_paper_trade(minute_policy_paper_trade_id),
  minute_policy_path_id BIGINT NOT NULL REFERENCES minute_ma_policy_path(minute_policy_path_id),
  realized_pnl NUMERIC NOT NULL,
  capital_after NUMERIC NOT NULL,
  settled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- V1 LIVE identity is policy-path scoped.  It must never reuse the legacy
-- minute_path operation/capital identity.
CREATE TABLE IF NOT EXISTS minute_ma_policy_operation (
  minute_policy_operation_id BIGSERIAL PRIMARY KEY,
  minute_policy_path_id BIGINT NOT NULL REFERENCES minute_ma_policy_path(minute_policy_path_id),
  operation_status VARCHAR(16) NOT NULL CHECK (operation_status IN ('PAPER','LIVE')),
  allocated_amount NUMERIC NOT NULL CHECK (
    (operation_status='PAPER' AND allocated_amount>=0) OR
    (operation_status='LIVE' AND allocated_amount>0)),
  capital_epoch_no INTEGER NOT NULL DEFAULT 0 CHECK (capital_epoch_no>=0),
  effective_from TIMESTAMP NOT NULL,
  effective_to TIMESTAMP,
  change_reason VARCHAR(16) NOT NULL CHECK (change_reason IN ('MANUAL','AUTO','RISK','ADMIN')),
  audit_reference TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (effective_to IS NULL OR effective_to>effective_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_minute_ma_policy_operation_current
  ON minute_ma_policy_operation(minute_policy_path_id) WHERE effective_to IS NULL;

CREATE TABLE IF NOT EXISTS minute_ma_policy_compound_capital (
  minute_policy_path_id BIGINT NOT NULL REFERENCES minute_ma_policy_path(minute_policy_path_id),
  capital_epoch_no INTEGER NOT NULL CHECK (capital_epoch_no>0),
  source_policy_operation_id BIGINT NOT NULL UNIQUE
    REFERENCES minute_ma_policy_operation(minute_policy_operation_id),
  epoch_initial_capital NUMERIC NOT NULL CHECK (epoch_initial_capital>0),
  strategy_compound_capital NUMERIC NOT NULL,
  cumulative_net_realized_pnl NUMERIC NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(minute_policy_path_id,capital_epoch_no),
  CHECK (strategy_compound_capital=epoch_initial_capital+cumulative_net_realized_pnl)
);

-- Existing selection rows remain untouched; scoped consumers can select the
-- latest APPROVED batch independently for legacy research and V1 operation.
ALTER TABLE minute_ma_selection_batch
  ADD COLUMN IF NOT EXISTS selection_scope VARCHAR(64) NOT NULL DEFAULT 'LEGACY_RESEARCH',
  ADD COLUMN IF NOT EXISTS policy_version VARCHAR(32),
  ADD COLUMN IF NOT EXISTS selection_purpose VARCHAR(16) NOT NULL DEFAULT 'RESEARCH'
    CHECK (selection_purpose IN ('RESEARCH','OPERATION'));
CREATE INDEX IF NOT EXISTS ix_minute_ma_selection_scope
  ON minute_ma_selection_batch(selection_scope,status,selected_at DESC);

ALTER TABLE minute_ma_selection_snapshot
  ADD COLUMN IF NOT EXISTS minute_policy_path_id BIGINT REFERENCES minute_ma_policy_path(minute_policy_path_id),
  ADD COLUMN IF NOT EXISTS source_daily_strategy_id VARCHAR(20)
    REFERENCES daily_strategy_master(strategy_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_minute_ma_selection_policy_snapshot
  ON minute_ma_selection_snapshot(selection_batch_id,minute_policy_path_id)
  WHERE minute_policy_path_id IS NOT NULL;

CREATE OR REPLACE VIEW vw_minute_ma_current_selection AS
WITH b AS (
  SELECT selection_batch_id FROM minute_ma_selection_batch
   WHERE status='APPROVED' AND selection_scope='LEGACY_RESEARCH'
   ORDER BY selected_at DESC,selection_batch_id DESC LIMIT 1
)
SELECT s.* FROM minute_ma_selection_snapshot s JOIN b USING(selection_batch_id);

CREATE OR REPLACE VIEW vw_minute_ma_current_selection_scoped AS
WITH latest AS (
  SELECT DISTINCT ON (selection_scope) selection_scope,selection_batch_id
    FROM minute_ma_selection_batch
   WHERE status='APPROVED'
   ORDER BY selection_scope,selected_at DESC,selection_batch_id DESC
)
SELECT l.selection_scope,b.policy_version,b.selection_purpose,s.*
  FROM latest l
  JOIN minute_ma_selection_batch b USING(selection_batch_id)
  JOIN minute_ma_selection_snapshot s USING(selection_batch_id);

CREATE OR REPLACE VIEW vw_minute_ma_v1_current_selection AS
SELECT * FROM vw_minute_ma_current_selection_scoped
 WHERE selection_scope='MINUTE_MA_V1_OPERATION'
   AND policy_version='V1.0'
   AND selection_purpose='OPERATION';

-- Durable live STOP identity augments the shared execution/ownership ledger.
ALTER TABLE minute_ma_live_signal_event
  ADD COLUMN IF NOT EXISTS minute_policy_path_id BIGINT REFERENCES minute_ma_policy_path(minute_policy_path_id),
  ADD COLUMN IF NOT EXISTS event_reason VARCHAR(24);
ALTER TABLE minute_ma_live_trade
  ALTER COLUMN operation_id DROP NOT NULL,
  ADD COLUMN IF NOT EXISTS minute_policy_path_id BIGINT REFERENCES minute_ma_policy_path(minute_policy_path_id),
  ADD COLUMN IF NOT EXISTS minute_policy_operation_id BIGINT
    REFERENCES minute_ma_policy_operation(minute_policy_operation_id),
  ADD COLUMN IF NOT EXISTS underlying_entry_reference_price NUMERIC,
  ADD COLUMN IF NOT EXISTS stop_threshold_price NUMERIC,
  ADD COLUMN IF NOT EXISTS stop_policy VARCHAR(48),
  ADD COLUMN IF NOT EXISTS stop_trigger_time TIMESTAMP,
  ADD COLUMN IF NOT EXISTS stop_trigger_underlying_close NUMERIC;
ALTER TABLE minute_ma_live_intent
  ADD COLUMN IF NOT EXISTS minute_policy_path_id BIGINT REFERENCES minute_ma_policy_path(minute_policy_path_id),
  ADD COLUMN IF NOT EXISTS minute_policy_operation_id BIGINT
    REFERENCES minute_ma_policy_operation(minute_policy_operation_id),
  ADD COLUMN IF NOT EXISTS target_minute_live_trade_id BIGINT REFERENCES minute_ma_live_trade(minute_live_trade_id),
  ADD COLUMN IF NOT EXISTS exit_reason VARCHAR(24),
  ADD COLUMN IF NOT EXISTS underlying_entry_reference_price NUMERIC,
  ADD COLUMN IF NOT EXISTS stop_threshold_price NUMERIC,
  ADD COLUMN IF NOT EXISTS stop_policy VARCHAR(48);
CREATE UNIQUE INDEX IF NOT EXISTS ux_minute_ma_v1_stop_intent
  ON minute_ma_live_intent(target_minute_live_trade_id,exit_reason)
  WHERE exit_reason='STOP_EXIT';

ALTER TABLE minute_ma_live_entry_skip
  ADD COLUMN IF NOT EXISTS minute_policy_path_id BIGINT REFERENCES minute_ma_policy_path(minute_policy_path_id),
  ADD COLUMN IF NOT EXISTS minute_policy_operation_id BIGINT
    REFERENCES minute_ma_policy_operation(minute_policy_operation_id);

ALTER TABLE minute_ma_live_capital_settlement
  ADD COLUMN IF NOT EXISTS minute_policy_path_id BIGINT REFERENCES minute_ma_policy_path(minute_policy_path_id),
  ADD COLUMN IF NOT EXISTS minute_policy_operation_id BIGINT
    REFERENCES minute_ma_policy_operation(minute_policy_operation_id);

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_minute_ma_live_trade_operation_identity') THEN
    ALTER TABLE minute_ma_live_trade ADD CONSTRAINT ck_minute_ma_live_trade_operation_identity CHECK (
      (minute_policy_path_id IS NULL AND operation_id IS NOT NULL AND minute_policy_operation_id IS NULL)
      OR
      (minute_policy_path_id IS NOT NULL AND operation_id IS NULL AND minute_policy_operation_id IS NOT NULL)
    );
  END IF;
END $$;

-- Approval evidence only.  It is deliberately not an Operation/Capital write.
CREATE TABLE IF NOT EXISTS minute_ma_v1_candidate_plan (
  source_daily_strategy_id VARCHAR(20) PRIMARY KEY REFERENCES daily_strategy_master(strategy_id),
  policy_code VARCHAR(64) NOT NULL REFERENCES minute_ma_operation_policy(policy_code),
  proposed_initial_capital NUMERIC NOT NULL CHECK (proposed_initial_capital>0),
  candidate_class VARCHAR(32) NOT NULL,
  approval_status VARCHAR(16) NOT NULL DEFAULT 'HOLD' CHECK (approval_status='HOLD'),
  source_reference VARCHAR(128) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO minute_ma_v1_candidate_plan
  (source_daily_strategy_id,policy_code,proposed_initial_capital,candidate_class,source_reference)
VALUES
 ('DS003848','MINUTE_MA_V1_LONG',500000,'STRONG_CORE','MINUTE_MA_V1_20260828'),
 ('DS002431','MINUTE_MA_V1_LONG',500000,'STRONG_CORE','MINUTE_MA_V1_20260828'),
 ('DS002527','MINUTE_MA_V1_LONG',500000,'STRONG_CORE','MINUTE_MA_V1_20260828'),
 ('DS002528','MINUTE_MA_V1_LONG',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003850','MINUTE_MA_V1_LONG',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS002479','MINUTE_MA_V1_LONG',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS002480','MINUTE_MA_V1_LONG',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003852','MINUTE_MA_V1_LONG',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003851','MINUTE_MA_V1_LONG',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003847','MINUTE_MA_V1_LONG',100000,'AGGRESSIVE','MINUTE_MA_V1_20260828'),
 ('DS003888','MINUTE_MA_V1_SHORT',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003887','MINUTE_MA_V1_SHORT',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS004368','MINUTE_MA_V1_SHORT',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003883','MINUTE_MA_V1_SHORT',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003864','MINUTE_MA_V1_SHORT',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003863','MINUTE_MA_V1_SHORT',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003862','MINUTE_MA_V1_SHORT',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003384','MINUTE_MA_V1_SHORT',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS002928','MINUTE_MA_V1_SHORT',100000,'CORE','MINUTE_MA_V1_20260828'),
 ('DS003859','MINUTE_MA_V1_SHORT',100000,'CORE','MINUTE_MA_V1_20260828')
ON CONFLICT (source_daily_strategy_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS minute_ma_v1_daily_telemetry_snapshot (
  snapshot_date DATE NOT NULL,
  minute_policy_path_id BIGINT NOT NULL REFERENCES minute_ma_policy_path(minute_policy_path_id),
  recent_5_compound_pct NUMERIC,
  rank_no INTEGER,
  top20_consecutive_days INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(snapshot_date,minute_policy_path_id)
);

CREATE OR REPLACE VIEW vw_minute_ma_v1_policy_dashboard AS
WITH closed0 AS (
  SELECT t.*,CASE WHEN net_return_pct>0 THEN 1 WHEN net_return_pct<0 THEN -1 ELSE 0 END result_sign,
    row_number() OVER(PARTITION BY minute_policy_path_id ORDER BY exit_execution_time DESC,minute_policy_paper_trade_id DESC) rn
  FROM minute_ma_policy_paper_trade t WHERE trade_status='CLOSED'
), closed AS (
  SELECT c.*,first_value(result_sign) OVER(PARTITION BY minute_policy_path_id ORDER BY rn) latest_sign
  FROM closed0 c
), metrics AS (
  SELECT minute_policy_path_id,
    count(*)::int closed_trade_count,
    count(*) FILTER(WHERE exit_reason='STOP_EXIT')::int stop_exit_count,
    count(*) FILTER(WHERE exit_execution_time::date>entry_execution_time::date)::int overnight_closed_count,
    100*(exp(sum(ln(1+net_return_pct/100)) FILTER(WHERE rn<=3))-1) recent_3_compound_pct,
    100*(exp(sum(ln(1+net_return_pct/100)) FILTER(WHERE rn<=5))-1) recent_5_compound_pct,
    100*(exp(sum(ln(1+net_return_pct/100)) FILTER(WHERE rn<=10))-1) recent_10_compound_pct,
    100*(exp(sum(ln(1+net_return_pct/100)) FILTER(WHERE rn BETWEEN 6 AND 10))-1) prior_5_compound_pct,
    max(latest_sign) latest_sign,
    (COALESCE(min(rn) FILTER(WHERE result_sign<>latest_sign),max(rn)+1)-1)::int current_streak_count
  FROM closed GROUP BY minute_policy_path_id
), latest_price AS (
  SELECT DISTINCT ON (stock_code) stock_code,close_price::numeric current_underlying_price
  FROM raw_stock_minute WHERE data_source='KIS' AND trading_venue='KRX' AND collect_cycle='1MIN'
    AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'
  ORDER BY stock_code,bar_time DESC,collected_at DESC NULLS LAST
), open_trade AS (
  SELECT t.minute_policy_path_id,
    count(*) FILTER(WHERE t.entry_execution_time::date<CURRENT_DATE)::int overnight_open_count,
    count(*)::int total_open_count,min(t.underlying_entry_reference_price) stop_anchor_min,
    max(t.underlying_entry_reference_price) stop_anchor_max,
    min(t.stop_threshold_price) stop_threshold_min,max(t.stop_threshold_price) stop_threshold_max,
    max(CASE WHEN op.stop_direction='ABOVE'
             THEN 100*(lp.current_underlying_price/t.underlying_entry_reference_price-1)
             ELSE 100*(1-lp.current_underlying_price/t.underlying_entry_reference_price) END) current_adverse_pct
  FROM minute_ma_policy_paper_trade t JOIN minute_ma_policy_path pp USING(minute_policy_path_id)
  JOIN minute_ma_operation_policy op USING(policy_code)
  JOIN minute_ma_path p USING(minute_path_id) JOIN minute_ma_strategy_master s USING(minute_strategy_id)
  LEFT JOIN latest_price lp ON lp.stock_code=s.signal_code
  WHERE t.trade_status='OPEN' GROUP BY t.minute_policy_path_id
), prior_rank AS (
  SELECT DISTINCT ON (minute_policy_path_id) minute_policy_path_id,rank_no prior_rank_no,top20_consecutive_days
  FROM minute_ma_v1_daily_telemetry_snapshot WHERE snapshot_date<CURRENT_DATE
  ORDER BY minute_policy_path_id,snapshot_date DESC
), live_perf AS (
  SELECT minute_policy_path_id,count(*) FILTER(WHERE trade_status='CLOSED')::int live_closed_count,
    sum(net_realized_pnl) FILTER(WHERE trade_status='CLOSED') live_net_realized_pnl
  FROM minute_ma_live_trade WHERE minute_policy_path_id IS NOT NULL GROUP BY minute_policy_path_id
), base AS (
 SELECT pp.minute_policy_path_id,pp.policy_path_key,pp.minute_path_id,op.policy_code,op.policy_version,
       op.direction,op.paper_entry_start,op.paper_entry_end,op.live_entry_start,op.live_entry_end,
       op.holding_policy,op.stop_policy,op.stop_percent,
       s.source_daily_strategy_id,s.signal_code,s.execution_code,
       s.entry_fast_ma,s.entry_slow_ma,s.exit_fast_ma,s.exit_slow_ma,s.trend_ma,
       COALESCE(m.closed_trade_count,0) closed_trade_count,COALESCE(m.stop_exit_count,0) stop_exit_count,
       CASE WHEN COALESCE(m.closed_trade_count,0)=0 THEN NULL ELSE 100.0*m.stop_exit_count/m.closed_trade_count END stop_frequency_pct,
       COALESCE(o.overnight_open_count,0) overnight_open_count,COALESCE(o.total_open_count,0) total_open_count,
       o.stop_anchor_min,o.stop_anchor_max,o.stop_threshold_min,o.stop_threshold_max,o.current_adverse_pct,
       m.recent_3_compound_pct,m.recent_5_compound_pct,m.recent_10_compound_pct,m.prior_5_compound_pct,
       m.recent_5_compound_pct-m.prior_5_compound_pct compound_acceleration_pct,
       CASE m.latest_sign WHEN 1 THEN 'WIN' WHEN -1 THEN 'LOSS' ELSE NULL END current_streak_type,
       COALESCE(m.current_streak_count,0) current_streak_count,
       pc.current_capital paper_compound_capital,COALESCE(lp.live_closed_count,0) live_closed_count,
       lp.live_net_realized_pnl,c.proposed_initial_capital,c.candidate_class,c.approval_status,
       sel.decision_status v1_selection_status,sel.recommended_amount,sel.approved_amount,
       po.operation_status v1_operation_status,po.allocated_amount v1_allocated_amount,
       po.capital_epoch_no v1_capital_epoch_no,
       cc.strategy_compound_capital v1_strategy_compound_capital,
       cc.cumulative_net_realized_pnl v1_cumulative_net_realized_pnl,
       pr.prior_rank_no,COALESCE(pr.top20_consecutive_days,0) top20_consecutive_days
  FROM minute_ma_policy_path pp JOIN minute_ma_operation_policy op USING(policy_code)
  JOIN minute_ma_path p USING(minute_path_id) JOIN minute_ma_strategy_master s USING(minute_strategy_id)
  LEFT JOIN metrics m USING(minute_policy_path_id) LEFT JOIN open_trade o USING(minute_policy_path_id)
  LEFT JOIN minute_ma_policy_paper_capital pc USING(minute_policy_path_id)
  LEFT JOIN minute_ma_v1_candidate_plan c USING(source_daily_strategy_id)
  LEFT JOIN vw_minute_ma_v1_current_selection sel USING(minute_policy_path_id)
  LEFT JOIN minute_ma_policy_operation po
    ON po.minute_policy_path_id=pp.minute_policy_path_id AND po.effective_to IS NULL
  LEFT JOIN minute_ma_policy_compound_capital cc
    ON cc.minute_policy_path_id=po.minute_policy_path_id
   AND cc.capital_epoch_no=po.capital_epoch_no
  LEFT JOIN prior_rank pr USING(minute_policy_path_id) LEFT JOIN live_perf lp USING(minute_policy_path_id)
 WHERE pp.is_enabled='Y'
), ranked AS (
 SELECT base.*,CASE WHEN recent_5_compound_pct IS NULL THEN NULL ELSE
   rank() OVER(ORDER BY recent_5_compound_pct DESC NULLS LAST,minute_policy_path_id)::int END current_rank
 FROM base
)
SELECT ranked.*,
 CASE WHEN current_rank IS NOT NULL AND current_rank<=20 THEN top20_consecutive_days+1 ELSE 0 END current_top20_consecutive_days
FROM ranked;

DO $$ DECLARE n bigint; legacy bigint; capital numeric; BEGIN
  SELECT count(*) INTO n FROM minute_ma_policy_path WHERE is_enabled='Y';
  IF n<>2400 THEN RAISE EXCEPTION 'V1 policy path invariant failed: %',n; END IF;
  SELECT count(*) INTO legacy FROM minute_ma_path WHERE is_enabled='Y';
  IF legacy<>19200 THEN RAISE EXCEPTION 'legacy path count changed: %',legacy; END IF;
  SELECT sum(proposed_initial_capital) INTO capital FROM minute_ma_v1_candidate_plan;
  IF capital<>3200000 THEN RAISE EXCEPTION 'candidate capital invariant failed: %',capital; END IF;
END $$;

COMMIT;
