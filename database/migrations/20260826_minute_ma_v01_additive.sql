-- Trading System V2 minute MA four-axis runtime (additive only).
-- Research 802, daily_strategy_*, analysis_multi_ma_* and RAW are not mutated.
BEGIN;

DO $$ BEGIN
  IF to_regclass('public.live_order_request') IS NULL
     OR to_regclass('public.live_broker_order') IS NULL
     OR to_regclass('public.execution_logical_position') IS NULL
     OR to_regclass('public.execution_fill_allocation') IS NULL
     OR to_regclass('public.execution_reconciliation_audit') IS NULL THEN
    RAISE EXCEPTION 'shared broker planning/ownership DDL prerequisite is missing';
  END IF;
END $$;

DO $$
DECLARE n bigint;
BEGIN
  SELECT count(*) INTO n
    FROM daily_strategy_master
   WHERE strategy_role='CANONICAL' AND is_enabled='Y';
  IF n <> 2400 THEN
    RAISE EXCEPTION 'minute MA seed requires exactly 2400 canonical daily semantics; got %', n;
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS minute_ma_strategy_master (
  minute_strategy_id BIGSERIAL PRIMARY KEY,
  strategy_key VARCHAR(180) NOT NULL UNIQUE,
  source_daily_strategy_id VARCHAR(20) NOT NULL UNIQUE
    REFERENCES daily_strategy_master(strategy_id),
  signal_code VARCHAR(32) NOT NULL,
  execution_code VARCHAR(32) NOT NULL,
  direction VARCHAR(8) NOT NULL CHECK (direction IN ('LONG','SHORT')),
  entry_fast_ma INTEGER NOT NULL CHECK (entry_fast_ma > 0),
  entry_slow_ma INTEGER NOT NULL CHECK (entry_slow_ma > entry_fast_ma),
  exit_fast_ma INTEGER NOT NULL CHECK (exit_fast_ma > 0),
  exit_slow_ma INTEGER NOT NULL CHECK (exit_slow_ma > exit_fast_ma),
  trend_ma INTEGER,
  is_enabled CHAR(1) NOT NULL DEFAULT 'Y' CHECK (is_enabled IN ('Y','N')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO minute_ma_strategy_master(
  strategy_key,source_daily_strategy_id,signal_code,execution_code,direction,
  entry_fast_ma,entry_slow_ma,exit_fast_ma,exit_slow_ma,trend_ma
)
SELECT format('MINUTE_MA|%s|%s|E%s_%s|X%s_%s|T%s',
              signal_code,direction,entry_fast_ma,entry_slow_ma,
              exit_fast_ma,exit_slow_ma,COALESCE(trend_ma::text,'NONE')),
       strategy_id,signal_code,execution_code,direction,
       entry_fast_ma,entry_slow_ma,exit_fast_ma,exit_slow_ma,trend_ma
  FROM daily_strategy_master
 WHERE strategy_role='CANONICAL' AND is_enabled='Y'
 ORDER BY strategy_id
ON CONFLICT (source_daily_strategy_id) DO NOTHING;

CREATE TABLE IF NOT EXISTS minute_ma_path (
  minute_path_id BIGSERIAL PRIMARY KEY,
  minute_strategy_id BIGINT NOT NULL REFERENCES minute_ma_strategy_master(minute_strategy_id),
  path_key VARCHAR(220) NOT NULL UNIQUE,
  data_axis VARCHAR(32) NOT NULL CHECK (data_axis IN (
    'KRX_CONTINUOUS','KRX_RESET','INTEGRATED_CONTINUOUS','INTEGRATED_RESET')),
  market_source VARCHAR(16) NOT NULL CHECK (market_source IN ('KRX','INTEGRATED')),
  continuity_mode VARCHAR(16) NOT NULL CHECK (continuity_mode IN ('CONTINUOUS','RESET')),
  session_start TIME NOT NULL,
  session_end TIME NOT NULL,
  historical_status VARCHAR(16) NOT NULL CHECK (historical_status IN ('AVAILABLE','PENDING')),
  is_enabled CHAR(1) NOT NULL DEFAULT 'Y' CHECK (is_enabled IN ('Y','N')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (minute_strategy_id,data_axis),
  CHECK (session_end > session_start)
);

INSERT INTO minute_ma_path(
  minute_strategy_id,path_key,data_axis,market_source,continuity_mode,
  session_start,session_end,historical_status
)
SELECT s.minute_strategy_id, s.strategy_key||'|'||a.data_axis, a.data_axis,
       a.market_source,a.continuity_mode,a.session_start,a.session_end,a.historical_status
  FROM minute_ma_strategy_master s
 CROSS JOIN (VALUES
   ('KRX_CONTINUOUS','KRX','CONTINUOUS',TIME '09:00',TIME '15:30','AVAILABLE'),
   ('KRX_RESET','KRX','RESET',TIME '09:00',TIME '15:30','AVAILABLE'),
   ('INTEGRATED_CONTINUOUS','INTEGRATED','CONTINUOUS',TIME '08:00',TIME '19:59','AVAILABLE'),
   ('INTEGRATED_RESET','INTEGRATED','RESET',TIME '08:00',TIME '19:59','PENDING')
 ) AS a(data_axis,market_source,continuity_mode,session_start,session_end,historical_status)
ON CONFLICT (minute_strategy_id,data_axis) DO NOTHING;

CREATE TABLE IF NOT EXISTS minute_ma_paper_event (
  minute_paper_event_id BIGSERIAL PRIMARY KEY,
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  signal_event_key CHAR(64) NOT NULL,
  event_type VARCHAR(16) NOT NULL CHECK (event_type IN ('ENTRY','EXIT','EOD_EXIT')),
  source_bar_time TIMESTAMP NOT NULL,
  confirmed_at TIMESTAMP NOT NULL,
  proxy_bar_time TIMESTAMP,
  proxy_price NUMERIC,
  event_status VARCHAR(32) NOT NULL CHECK (event_status IN (
    'CREATED','NO_PROXY_BAR','OUTSIDE_KRX_EXECUTION_WINDOW')),
  source_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (minute_path_id,signal_event_key,event_type)
);

CREATE TABLE IF NOT EXISTS minute_ma_paper_trade (
  minute_paper_trade_id BIGSERIAL PRIMARY KEY,
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  entry_event_key CHAR(64) NOT NULL,
  trade_status VARCHAR(16) NOT NULL CHECK (trade_status IN ('OPEN','CLOSED','CANCELLED')),
  entry_signal_time TIMESTAMP NOT NULL,
  entry_execution_time TIMESTAMP NOT NULL,
  entry_price NUMERIC NOT NULL CHECK (entry_price > 0),
  exit_signal_time TIMESTAMP,
  exit_execution_time TIMESTAMP,
  exit_price NUMERIC CHECK (exit_price IS NULL OR exit_price > 0),
  exit_reason VARCHAR(16) CHECK (exit_reason IS NULL OR exit_reason IN ('NORMAL_EXIT','EOD_1519')),
  gross_return_pct NUMERIC,
  net_return_pct NUMERIC,
  basis_capital NUMERIC NOT NULL CHECK (basis_capital > 0),
  realized_pnl NUMERIC,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (minute_path_id,entry_event_key),
  CHECK ((trade_status='OPEN' AND exit_execution_time IS NULL) OR
         (trade_status<>'OPEN' AND exit_execution_time IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_minute_ma_paper_trade_open
  ON minute_ma_paper_trade(minute_path_id,entry_execution_time)
  WHERE trade_status='OPEN';

CREATE TABLE IF NOT EXISTS minute_ma_paper_capital (
  minute_path_id BIGINT PRIMARY KEY REFERENCES minute_ma_path(minute_path_id),
  initial_capital NUMERIC NOT NULL DEFAULT 1000000 CHECK (initial_capital>0),
  current_capital NUMERIC NOT NULL DEFAULT 1000000,
  cumulative_realized_pnl NUMERIC NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (current_capital=initial_capital+cumulative_realized_pnl)
);
INSERT INTO minute_ma_paper_capital(minute_path_id)
SELECT minute_path_id FROM minute_ma_path ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS minute_ma_paper_settlement (
  minute_paper_trade_id BIGINT PRIMARY KEY REFERENCES minute_ma_paper_trade(minute_paper_trade_id),
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  realized_pnl NUMERIC NOT NULL,
  capital_after NUMERIC NOT NULL,
  settled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS minute_ma_runtime_cursor (
  runtime_name VARCHAR(64) NOT NULL,
  data_axis VARCHAR(32) NOT NULL,
  signal_code VARCHAR(32) NOT NULL,
  last_source_bar_time TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(runtime_name,data_axis,signal_code)
);

CREATE TABLE IF NOT EXISTS minute_ma_selection_batch (
  selection_batch_id VARCHAR(64) PRIMARY KEY,
  selected_at TIMESTAMP NOT NULL,
  evaluation_from DATE NOT NULL,
  evaluation_to DATE NOT NULL,
  metric_contract_version VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL CHECK (status IN ('DRAFT','APPROVED','SUPERSEDED')),
  source_artifacts JSONB NOT NULL,
  description TEXT NOT NULL,
  created_by VARCHAR(64) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (evaluation_to >= evaluation_from)
);

CREATE TABLE IF NOT EXISTS minute_ma_selection_snapshot (
  selection_batch_id VARCHAR(64) NOT NULL REFERENCES minute_ma_selection_batch(selection_batch_id),
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  evaluation_rank INTEGER,
  decision_status VARCHAR(16) NOT NULL CHECK (decision_status IN ('SELECTED','NOT_SELECTED','PENDING')),
  completed_trade_count INTEGER,
  win_rate_pct NUMERIC,
  avg_net_return_pct NUMERIC,
  median_net_return_pct NUMERIC,
  compound_return_pct NUMERIC,
  compound_profit NUMERIC,
  final_compound_capital NUMERIC,
  max_concurrent_open INTEGER,
  avg_hold_minutes NUMERIC,
  worst_trade_pct NUMERIC,
  mdd_pct NUMERIC,
  robustness_yn CHAR(1) NOT NULL CHECK (robustness_yn IN ('Y','N')),
  recommended_amount NUMERIC,
  approved_amount NUMERIC,
  reason_codes TEXT[] NOT NULL DEFAULT '{}',
  source_row JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(selection_batch_id,minute_path_id),
  CHECK ((decision_status='PENDING' AND completed_trade_count IS NULL AND compound_return_pct IS NULL)
      OR decision_status<>'PENDING')
);

CREATE OR REPLACE FUNCTION fn_minute_ma_selection_snapshot_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM minute_ma_selection_batch
              WHERE selection_batch_id=OLD.selection_batch_id AND status='APPROVED') THEN
    RAISE EXCEPTION 'APPROVED minute MA selection snapshot is immutable';
  END IF;
  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
DROP TRIGGER IF EXISTS trg_minute_ma_selection_snapshot_immutable ON minute_ma_selection_snapshot;
CREATE TRIGGER trg_minute_ma_selection_snapshot_immutable
BEFORE UPDATE OR DELETE ON minute_ma_selection_snapshot
FOR EACH ROW EXECUTE FUNCTION fn_minute_ma_selection_snapshot_immutable();

CREATE TABLE IF NOT EXISTS minute_ma_operation (
  operation_id BIGSERIAL PRIMARY KEY,
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
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
CREATE UNIQUE INDEX IF NOT EXISTS ux_minute_ma_operation_current
  ON minute_ma_operation(minute_path_id) WHERE effective_to IS NULL;

INSERT INTO minute_ma_operation(minute_path_id,operation_status,allocated_amount,capital_epoch_no,
                                effective_from,change_reason,audit_reference)
SELECT minute_path_id,'PAPER',0,0,CURRENT_TIMESTAMP,'ADMIN','MINUTE_MA_V01_INITIAL_PAPER'
  FROM minute_ma_path
ON CONFLICT (minute_path_id) WHERE effective_to IS NULL DO NOTHING;

CREATE TABLE IF NOT EXISTS minute_ma_compound_capital (
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  capital_epoch_no INTEGER NOT NULL CHECK (capital_epoch_no>=1),
  source_operation_id BIGINT NOT NULL UNIQUE REFERENCES minute_ma_operation(operation_id),
  epoch_initial_capital NUMERIC NOT NULL CHECK (epoch_initial_capital>0),
  strategy_compound_capital NUMERIC NOT NULL,
  cumulative_net_realized_pnl NUMERIC NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 0 CHECK (version>=0),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(minute_path_id,capital_epoch_no),
  CHECK (strategy_compound_capital=epoch_initial_capital+cumulative_net_realized_pnl)
);

CREATE TABLE IF NOT EXISTS minute_ma_live_trade (
  minute_live_trade_id BIGSERIAL PRIMARY KEY,
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  minute_paper_trade_id BIGINT NOT NULL UNIQUE REFERENCES minute_ma_paper_trade(minute_paper_trade_id),
  operation_id BIGINT NOT NULL REFERENCES minute_ma_operation(operation_id),
  capital_epoch_no INTEGER NOT NULL,
  ownership_id VARCHAR(120) NOT NULL UNIQUE,
  trade_status VARCHAR(16) NOT NULL CHECK (trade_status IN ('OPEN','CLOSED','CANCELLED')),
  capital_at_signal NUMERIC NOT NULL CHECK (capital_at_signal>0),
  entry_filled_amount NUMERIC NOT NULL DEFAULT 0,
  exit_filled_amount NUMERIC NOT NULL DEFAULT 0,
  gross_realized_pnl NUMERIC,
  net_realized_pnl NUMERIC,
  capital_applied_yn CHAR(1) NOT NULL DEFAULT 'N' CHECK (capital_applied_yn IN ('Y','N')),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS minute_ma_live_capital_settlement (
  settlement_id UUID PRIMARY KEY,
  minute_live_trade_id BIGINT NOT NULL UNIQUE REFERENCES minute_ma_live_trade(minute_live_trade_id),
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  capital_epoch_no INTEGER NOT NULL,
  entry_filled_amount NUMERIC NOT NULL,
  exit_filled_amount NUMERIC NOT NULL,
  gross_realized_pnl NUMERIC NOT NULL,
  buy_fee NUMERIC NOT NULL DEFAULT 0,
  sell_fee NUMERIC NOT NULL DEFAULT 0,
  sell_tax NUMERIC NOT NULL DEFAULT 0,
  other_cost NUMERIC NOT NULL DEFAULT 0,
  net_realized_pnl NUMERIC NOT NULL,
  settled_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS minute_ma_live_intent (
  intent_id UUID PRIMARY KEY,
  intent_key CHAR(64) NOT NULL UNIQUE,
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  minute_paper_trade_id BIGINT NOT NULL REFERENCES minute_ma_paper_trade(minute_paper_trade_id),
  minute_live_trade_id BIGINT REFERENCES minute_ma_live_trade(minute_live_trade_id),
  intent_type VARCHAR(8) NOT NULL CHECK (intent_type IN ('ENTRY','EXIT')),
  source_event_time TIMESTAMP NOT NULL,
  reference_price NUMERIC CHECK (reference_price IS NULL OR reference_price>0),
  requested_quantity INTEGER NOT NULL CHECK (requested_quantity>=0),
  capital_at_signal NUMERIC NOT NULL CHECK (capital_at_signal>0),
  lifecycle_status VARCHAR(32) NOT NULL,
  block_reason VARCHAR(64),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS minute_ma_live_order_link (
  intent_id UUID PRIMARY KEY REFERENCES minute_ma_live_intent(intent_id),
  order_request_id UUID NOT NULL UNIQUE REFERENCES live_order_request(order_request_id),
  broker_order_id UUID UNIQUE REFERENCES live_broker_order(broker_order_id),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS minute_ma_live_capital_reservation (
  intent_id UUID PRIMARY KEY REFERENCES minute_ma_live_intent(intent_id),
  reserved_amount NUMERIC NOT NULL CHECK (reserved_amount>=0),
  consumed_amount NUMERIC NOT NULL DEFAULT 0 CHECK (consumed_amount>=0),
  released_amount NUMERIC NOT NULL DEFAULT 0 CHECK (released_amount>=0),
  reservation_status VARCHAR(32) NOT NULL CHECK (reservation_status IN (
    'RESERVED','PARTIALLY_CONSUMED','CONSUMED','RELEASED')),
  remaining_reserved_amount NUMERIC GENERATED ALWAYS AS
    (reserved_amount-consumed_amount-released_amount) STORED,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CHECK (reserved_amount-consumed_amount-released_amount>=0)
);

CREATE TABLE IF NOT EXISTS minute_ma_live_entry_skip (
  skip_id UUID PRIMARY KEY,
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  minute_paper_trade_id BIGINT NOT NULL REFERENCES minute_ma_paper_trade(minute_paper_trade_id),
  signal_event_key CHAR(64) NOT NULL,
  capital_epoch_no INTEGER NOT NULL,
  capital_at_signal NUMERIC NOT NULL,
  planned_quantity INTEGER NOT NULL,
  planned_notional NUMERIC NOT NULL,
  skip_reason VARCHAR(64) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(minute_path_id,signal_event_key)
);

CREATE TABLE IF NOT EXISTS minute_ma_send_profile (
  profile_code VARCHAR(64) PRIMARY KEY,
  send_enabled CHAR(1) NOT NULL CHECK (send_enabled IN ('Y','N')),
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_by VARCHAR(64) NOT NULL
);
INSERT INTO minute_ma_send_profile(profile_code,send_enabled,updated_by)
VALUES ('MINUTE_MA_LIVE_SEND','N','MIGRATION_DEFAULT_LOCKED')
ON CONFLICT (profile_code) DO NOTHING;

CREATE OR REPLACE VIEW vw_minute_ma_current_selection AS
WITH b AS (
  SELECT selection_batch_id
    FROM minute_ma_selection_batch
   WHERE status='APPROVED'
   ORDER BY selected_at DESC,selection_batch_id DESC LIMIT 1
)
SELECT s.* FROM minute_ma_selection_snapshot s JOIN b USING(selection_batch_id);

CREATE OR REPLACE VIEW vw_minute_ma_dashboard AS
WITH perf AS (
  SELECT minute_path_id,
         count(*) FILTER (WHERE trade_status='CLOSED')::int AS trade_count,
         100.0*count(*) FILTER (WHERE trade_status='CLOSED' AND net_return_pct>0)
           /NULLIF(count(*) FILTER (WHERE trade_status='CLOSED'),0) AS win_rate_pct,
         avg(net_return_pct) FILTER (WHERE trade_status='CLOSED') AS avg_net_return_pct,
         percentile_cont(0.5) WITHIN GROUP (ORDER BY net_return_pct)
           FILTER (WHERE trade_status='CLOSED') AS median_net_return_pct,
         min(net_return_pct) FILTER (WHERE trade_status='CLOSED') AS worst_trade_pct
    FROM minute_ma_paper_trade GROUP BY minute_path_id
), current_operation AS (
  SELECT * FROM minute_ma_operation WHERE effective_to IS NULL
), current_capital AS (
  SELECT DISTINCT ON (minute_path_id) minute_path_id,capital_epoch_no,strategy_compound_capital
    FROM minute_ma_compound_capital ORDER BY minute_path_id,capital_epoch_no DESC
), live_perf AS (
  SELECT minute_path_id,count(*) FILTER (WHERE trade_status='CLOSED')::int AS live_trade_count,
         sum(net_realized_pnl) FILTER (WHERE trade_status='CLOSED') AS live_net_realized_pnl
    FROM minute_ma_live_trade GROUP BY minute_path_id
)
SELECT p.minute_path_id,p.path_key,p.data_axis,p.market_source,p.continuity_mode,
       s.minute_strategy_id,s.source_daily_strategy_id,s.signal_code,s.execution_code,s.direction,
       s.entry_fast_ma,s.entry_slow_ma,s.exit_fast_ma,s.exit_slow_ma,s.trend_ma,
       COALESCE(sel.decision_status,'PENDING') AS selection_status,
       COALESCE(sel.robustness_yn,'N') AS robustness_yn,
       o.operation_status,o.allocated_amount,c.capital_epoch_no,c.strategy_compound_capital,
       COALESCE(perf.trade_count,0) AS paper_trade_count,perf.win_rate_pct,
       perf.avg_net_return_pct,perf.median_net_return_pct,
       CASE WHEN COALESCE(perf.trade_count,0)=0 THEN NULL
            ELSE (pc.current_capital/pc.initial_capital-1)*100 END AS compound_return_pct,
       pc.current_capital AS paper_compound_capital,
       perf.worst_trade_pct,sel.max_concurrent_open,sel.avg_hold_minutes,sel.mdd_pct,
       COALESCE(lp.live_trade_count,0) AS live_trade_count,lp.live_net_realized_pnl
  FROM minute_ma_path p
  JOIN minute_ma_strategy_master s USING(minute_strategy_id)
  JOIN current_operation o USING(minute_path_id)
  LEFT JOIN vw_minute_ma_current_selection sel USING(minute_path_id)
  LEFT JOIN current_capital c USING(minute_path_id)
  LEFT JOIN minute_ma_paper_capital pc USING(minute_path_id)
  LEFT JOIN perf USING(minute_path_id)
  LEFT JOIN live_perf lp USING(minute_path_id)
 WHERE p.is_enabled='Y' AND s.is_enabled='Y';

DO $$
DECLARE strategies bigint; paths bigint; operations bigint;
BEGIN
  SELECT count(*) INTO strategies FROM minute_ma_strategy_master WHERE is_enabled='Y';
  SELECT count(*) INTO paths FROM minute_ma_path WHERE is_enabled='Y';
  SELECT count(*) INTO operations FROM minute_ma_operation WHERE effective_to IS NULL;
  IF strategies<>2400 OR paths<>9600 OR operations<>9600 THEN
    RAISE EXCEPTION 'minute MA seed invariant failed: strategies %, paths %, operations %',
      strategies,paths,operations;
  END IF;
END $$;

COMMIT;
