BEGIN;

-- V1.5 restores BASE as an additive fourth research axis.
ALTER TABLE minute_ma_real_variant
  DROP CONSTRAINT IF EXISTS minute_ma_real_variant_filter_code_check;
ALTER TABLE minute_ma_real_variant
  ADD CONSTRAINT minute_ma_real_variant_filter_code_check
  CHECK (filter_code IN ('BASE','REAL_F1','REAL_F2','REAL_F3'));
ALTER TABLE minute_ma_real_paper_trade
  DROP CONSTRAINT IF EXISTS minute_ma_real_paper_trade_filter_code_check;
ALTER TABLE minute_ma_real_paper_trade
  ADD CONSTRAINT minute_ma_real_paper_trade_filter_code_check
  CHECK (filter_code IN ('BASE','REAL_F1','REAL_F2','REAL_F3'));

INSERT INTO minute_ma_real_variant(
  minute_strategy_id,strategy_id,signal_code,filter_code,paper_epoch,
  initial_capital,effective_from)
SELECT minute_strategy_id,minute_strategy_id::text,signal_code,'BASE',1,
       10000000,DATE '2026-08-31'
FROM minute_ma_strategy_master
WHERE is_enabled='Y' AND direction='LONG'
ON CONFLICT(minute_strategy_id,filter_code,paper_epoch) DO NOTHING;

INSERT INTO minute_ma_real_capital_epoch(
  real_variant_id,paper_epoch,initial_capital,current_realized_capital,
  effective_from,reset_reason)
SELECT real_variant_id,1,10000000,10000000,DATE '2026-08-31','INITIAL_V1_5_BASE'
FROM minute_ma_real_variant
WHERE filter_code='BASE'
ON CONFLICT(real_variant_id,paper_epoch) DO NOTHING;

CREATE TABLE IF NOT EXISTS minute_ma_real_live_route (
  real_live_route_id BIGSERIAL PRIMARY KEY,
  real_variant_id BIGINT NOT NULL REFERENCES minute_ma_real_variant(real_variant_id),
  minute_path_id BIGINT NOT NULL REFERENCES minute_ma_path(minute_path_id),
  route_code VARCHAR(16) NOT NULL CHECK (route_code IN ('UNDERLYING','LEVERAGE')),
  execution_stock_code VARCHAR(6) NOT NULL,
  sizing_mode VARCHAR(16) NOT NULL CHECK (sizing_mode IN ('CAPITAL','FIXED_QTY')),
  allocated_amount NUMERIC(20,6) NOT NULL DEFAULT 0 CHECK (allocated_amount>=0),
  fixed_quantity INTEGER NOT NULL DEFAULT 0 CHECK (fixed_quantity>=0),
  capital_epoch_no INTEGER NOT NULL DEFAULT 1 CHECK (capital_epoch_no>0),
  activated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  effective_from TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  effective_to TIMESTAMP NULL,
  last_source_bar_time TIMESTAMP NULL,
  change_reason TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT minute_ma_real_live_route_sizing_check CHECK (
    (sizing_mode='CAPITAL' AND fixed_quantity=0) OR
    (sizing_mode='FIXED_QTY' AND allocated_amount=0))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_minute_ma_real_live_route_active
  ON minute_ma_real_live_route(real_variant_id,route_code) WHERE effective_to IS NULL;
CREATE INDEX IF NOT EXISTS ix_minute_ma_real_live_route_execution
  ON minute_ma_real_live_route(execution_stock_code) WHERE effective_to IS NULL;

CREATE TABLE IF NOT EXISTS minute_ma_real_live_capital (
  real_live_route_id BIGINT NOT NULL REFERENCES minute_ma_real_live_route(real_live_route_id),
  capital_epoch_no INTEGER NOT NULL,
  initial_capital NUMERIC(20,6) NOT NULL CHECK (initial_capital>=0),
  current_capital NUMERIC(20,6) NOT NULL,
  cumulative_net_realized_pnl NUMERIC(20,6) NOT NULL DEFAULT 0,
  effective_from TIMESTAMP NOT NULL,
  ended_at TIMESTAMP NULL,
  reset_reason TEXT NOT NULL,
  version BIGINT NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(real_live_route_id,capital_epoch_no)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_minute_ma_real_live_capital_active
  ON minute_ma_real_live_capital(real_live_route_id) WHERE ended_at IS NULL;

ALTER TABLE minute_ma_live_signal_event
  ADD COLUMN IF NOT EXISTS real_variant_id BIGINT REFERENCES minute_ma_real_variant(real_variant_id);
ALTER TABLE minute_ma_live_intent
  ADD COLUMN IF NOT EXISTS real_variant_id BIGINT REFERENCES minute_ma_real_variant(real_variant_id),
  ADD COLUMN IF NOT EXISTS real_live_route_id BIGINT REFERENCES minute_ma_real_live_route(real_live_route_id),
  ADD COLUMN IF NOT EXISTS real_capital_epoch_no INTEGER;
ALTER TABLE minute_ma_live_intent
  DROP CONSTRAINT IF EXISTS minute_ma_live_intent_capital_at_signal_check;
ALTER TABLE minute_ma_live_intent
  ADD CONSTRAINT minute_ma_live_intent_capital_at_signal_check CHECK (capital_at_signal>=0);
ALTER TABLE minute_ma_live_trade
  ADD COLUMN IF NOT EXISTS real_variant_id BIGINT REFERENCES minute_ma_real_variant(real_variant_id),
  ADD COLUMN IF NOT EXISTS real_live_route_id BIGINT REFERENCES minute_ma_real_live_route(real_live_route_id),
  ADD COLUMN IF NOT EXISTS real_capital_epoch_no INTEGER;
ALTER TABLE minute_ma_live_trade
  DROP CONSTRAINT IF EXISTS minute_ma_live_trade_capital_at_signal_check;
ALTER TABLE minute_ma_live_trade
  ADD CONSTRAINT minute_ma_live_trade_capital_at_signal_check CHECK (capital_at_signal>=0);
ALTER TABLE minute_ma_live_capital_settlement
  ADD COLUMN IF NOT EXISTS real_variant_id BIGINT REFERENCES minute_ma_real_variant(real_variant_id),
  ADD COLUMN IF NOT EXISTS real_live_route_id BIGINT REFERENCES minute_ma_real_live_route(real_live_route_id),
  ADD COLUMN IF NOT EXISTS real_capital_epoch_no INTEGER;

ALTER TABLE minute_ma_live_trade
  DROP CONSTRAINT IF EXISTS ck_minute_ma_live_trade_operation_identity;
ALTER TABLE minute_ma_live_trade
  ADD CONSTRAINT ck_minute_ma_live_trade_operation_identity CHECK (
    ((real_live_route_id IS NOT NULL) AND (real_variant_id IS NOT NULL)
      AND (real_capital_epoch_no IS NOT NULL) AND (operation_id IS NULL)
      AND (minute_policy_operation_id IS NULL)) OR
    ((real_live_route_id IS NULL) AND (real_variant_id IS NULL)
      AND (real_capital_epoch_no IS NULL) AND
      (((minute_policy_path_id IS NULL) AND (operation_id IS NOT NULL)
        AND (minute_policy_operation_id IS NULL)) OR
       ((minute_policy_path_id IS NOT NULL) AND (operation_id IS NULL)
        AND (minute_policy_operation_id IS NOT NULL))))
  );

CREATE TABLE IF NOT EXISTS minute_ma_real_live_entry_skip (
  real_live_route_id BIGINT NOT NULL REFERENCES minute_ma_real_live_route(real_live_route_id),
  signal_event_key CHAR(64) NOT NULL,
  source_event_time TIMESTAMP NOT NULL,
  capital_epoch_no INTEGER NOT NULL,
  capital_at_signal NUMERIC(20,6) NOT NULL,
  planned_quantity INTEGER NOT NULL,
  planned_notional NUMERIC(20,6) NOT NULL,
  skip_reason VARCHAR(64) NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(real_live_route_id,signal_event_key)
);

WITH top50(strategy_id,filter_code,execution_stock_code) AS (VALUES
 ('2161','REAL_F1','0193T0'),('1933','REAL_F1','0193W0'),('1693','REAL_F1','0193W0'),
 ('2163','REAL_F1','0193T0'),('2005','BASE','0193W0'),('1971','BASE','0193T0'),
 ('1201','REAL_F1','0193T0'),('1251','BASE','0193T0'),('1011','BASE','0193T0'),
 ('1981','BASE','0193W0'),('2030','BASE','0193W0'),('1491','BASE','0193T0'),
 ('1731','BASE','0193T0'),('2029','BASE','0193W0'),('469','BASE','0193W0'),
 ('2211','BASE','0193T0'),('771','BASE','0193T0'),('1934','REAL_F1','0193W0'),
 ('1189','BASE','0193W0'),('1453','REAL_F1','0193W0'),('1214','REAL_F1','0193W0'),
 ('2186','REAL_F2','0193T0'),('2221','BASE','0193W0'),('229','BASE','0193W0'),
 ('2185','REAL_F2','0193T0'),('1694','REAL_F1','0193W0'),('1921','REAL_F1','0193T0'),
 ('974','REAL_F1','0193W0'),('1227','REAL_F1','0193T0'),('234','BASE','0193W0'),
 ('1454','REAL_F1','0193W0'),('1947','REAL_F1','0193T0'),('2187','REAL_F2','0193T0'),
 ('2033','BASE','0193W0'),('472','BASE','0193W0'),('1226','REAL_F3','0193T0'),
 ('1261','REAL_F1','0193W0'),('986','REAL_F3','0193T0'),('1313','BASE','0193W0'),
 ('1923','REAL_F1','0193T0'),('987','REAL_F2','0193T0'),('1225','REAL_F3','0193T0'),
 ('746','REAL_F3','0193T0'),('1466','REAL_F3','0193T0'),('1790','BASE','0193W0'),
 ('1946','REAL_F3','0193T0'),('1789','BASE','0193W0'),('1707','REAL_F2','0193T0'),
 ('985','REAL_F3','0193T0'),('1706','REAL_F3','0193T0')
)
INSERT INTO minute_ma_real_live_route(
  real_variant_id,minute_path_id,route_code,execution_stock_code,sizing_mode,
  allocated_amount,fixed_quantity,capital_epoch_no,change_reason)
SELECT v.real_variant_id,p.minute_path_id,'LEVERAGE',t.execution_stock_code,
       'FIXED_QTY',0,1,1,'V1_5_TOP50_FIXED_ONE'
FROM top50 t
JOIN minute_ma_real_variant v ON v.strategy_id=t.strategy_id
 AND v.filter_code=t.filter_code AND v.effective_to IS NULL
JOIN minute_ma_path p ON p.minute_strategy_id=v.minute_strategy_id
 AND p.data_axis='KRX_RESET' AND p.is_enabled='Y'
ON CONFLICT DO NOTHING;

WITH underlying(strategy_id,filter_code,allocated_amount) AS (VALUES
 ('2161','REAL_F1',2000000::numeric),('2163','REAL_F1',0::numeric),
 ('2186','REAL_F2',0::numeric),('1934','REAL_F1',0::numeric),
 ('2185','REAL_F2',0::numeric)
)
INSERT INTO minute_ma_real_live_route(
  real_variant_id,minute_path_id,route_code,execution_stock_code,sizing_mode,
  allocated_amount,fixed_quantity,capital_epoch_no,change_reason)
SELECT v.real_variant_id,p.minute_path_id,'UNDERLYING',v.signal_code,
       'CAPITAL',u.allocated_amount,0,1,'V1_5_UNDERLYING_INITIAL'
FROM underlying u
JOIN minute_ma_real_variant v ON v.strategy_id=u.strategy_id
 AND v.filter_code=u.filter_code AND v.effective_to IS NULL
JOIN minute_ma_path p ON p.minute_strategy_id=v.minute_strategy_id
 AND p.data_axis='KRX_RESET' AND p.is_enabled='Y'
ON CONFLICT DO NOTHING;

INSERT INTO minute_ma_real_live_capital(
  real_live_route_id,capital_epoch_no,initial_capital,current_capital,
  effective_from,reset_reason)
SELECT real_live_route_id,capital_epoch_no,allocated_amount,allocated_amount,
       effective_from,'V1_5_INITIAL_ALLOCATION'
FROM minute_ma_real_live_route
ON CONFLICT(real_live_route_id,capital_epoch_no) DO NOTHING;

COMMIT;
