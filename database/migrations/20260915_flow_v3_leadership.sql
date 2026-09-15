-- Leadership-only additive schema. Never executed against production by this task.
-- Common-code INSERT is limited to the explicitly requested NEW research group.
BEGIN;
SET LOCAL lock_timeout='5s';
INSERT INTO common_code_group(group_cd,group_name,description,attr1,attr2,use_yn)
VALUES('FLOW_LEADERSHIP_CAPITAL','FLOW Leadership 연구자본','기존 매매 자본과 무관한 연구축',
       '초기 연구자본 KRW (양의 정수)','Dashboard 기본선택 Y/N','Y')
ON CONFLICT(group_cd) DO NOTHING;
INSERT INTO common_code(group_cd,code,code_name,attr1,attr2,sort_order,use_yn) VALUES
('FLOW_LEADERSHIP_CAPITAL','5000000','5,000,000원','5000000','N',1,'Y'),
('FLOW_LEADERSHIP_CAPITAL','6000000','6,000,000원','6000000','Y',2,'Y'),
('FLOW_LEADERSHIP_CAPITAL','10000000','10,000,000원','10000000','N',3,'Y'),
('FLOW_LEADERSHIP_CAPITAL','15000000','15,000,000원','15000000','N',4,'Y'),
('FLOW_LEADERSHIP_CAPITAL','20000000','20,000,000원','20000000','N',5,'Y'),
('FLOW_LEADERSHIP_CAPITAL','30000000','30,000,000원','30000000','N',6,'Y'),
('FLOW_LEADERSHIP_CAPITAL','40000000','40,000,000원','40000000','N',7,'Y'),
('FLOW_LEADERSHIP_CAPITAL','50000000','50,000,000원','50000000','N',8,'Y'),
('FLOW_LEADERSHIP_CAPITAL','60000000','60,000,000원','60000000','N',9,'Y'),
('FLOW_LEADERSHIP_CAPITAL','70000000','70,000,000원','70000000','N',10,'Y'),
('FLOW_LEADERSHIP_CAPITAL','80000000','80,000,000원','80000000','N',11,'Y'),
('FLOW_LEADERSHIP_CAPITAL','100000000','100,000,000원','100000000','N',12,'Y')
ON CONFLICT(group_cd,code) DO NOTHING;

CREATE TABLE IF NOT EXISTS flow_v3_leadership_run (
 snapshot_date date PRIMARY KEY,
 research_start date NOT NULL,
 version text NOT NULL,
 universe_count integer NOT NULL CHECK(universe_count>0),
 capital_count integer NOT NULL CHECK(capital_count>0),
 row_count integer NOT NULL CHECK(row_count=universe_count*capital_count),
 result_hash text NOT NULL,
 source_audit jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS flow_v3_leadership_snapshot (
 snapshot_date date NOT NULL REFERENCES flow_v3_leadership_run(snapshot_date),
 strategy_id varchar(20) NOT NULL,
 capital_base numeric(24,4) NOT NULL CHECK(capital_base>0),
 stock_code varchar(6) NOT NULL CHECK(stock_code IN ('000660','005930')),
 direction varchar(5) NOT NULL CHECK(direction='LONG'),
 strategy_definition jsonb NOT NULL,
 regular_daily jsonb NOT NULL CHECK(jsonb_typeof(regular_daily)='object'),
 regular_weekly jsonb NOT NULL CHECK(jsonb_typeof(regular_weekly)='object'),
 regular_monthly jsonb NOT NULL CHECK(jsonb_typeof(regular_monthly)='object'),
 extended_exit_daily jsonb NOT NULL CHECK(jsonb_typeof(extended_exit_daily)='object'),
 extended_exit_weekly jsonb NOT NULL CHECK(jsonb_typeof(extended_exit_weekly)='object'),
 extended_exit_monthly jsonb NOT NULL CHECK(jsonb_typeof(extended_exit_monthly)='object'),
 extended_full_daily jsonb NOT NULL CHECK(jsonb_typeof(extended_full_daily)='object'),
 extended_full_weekly jsonb NOT NULL CHECK(jsonb_typeof(extended_full_weekly)='object'),
 extended_full_monthly jsonb NOT NULL CHECK(jsonb_typeof(extended_full_monthly)='object'),
 after_daily jsonb NOT NULL CHECK(jsonb_typeof(after_daily)='object'),
 after_weekly jsonb NOT NULL CHECK(jsonb_typeof(after_weekly)='object'),
 after_monthly jsonb NOT NULL CHECK(jsonb_typeof(after_monthly)='object'),
 PRIMARY KEY(snapshot_date,strategy_id,capital_base)
);
CREATE INDEX IF NOT EXISTS ix_flow_leadership_history
 ON flow_v3_leadership_snapshot(capital_base,strategy_id,snapshot_date DESC);
CREATE INDEX IF NOT EXISTS ix_fl_regular_daily_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((regular_daily->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_regular_weekly_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((regular_weekly->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_regular_monthly_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((regular_monthly->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_extended_exit_daily_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((extended_exit_daily->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_extended_exit_weekly_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((extended_exit_weekly->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_extended_exit_monthly_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((extended_exit_monthly->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_extended_full_daily_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((extended_full_daily->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_extended_full_weekly_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((extended_full_weekly->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_extended_full_monthly_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((extended_full_monthly->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_after_daily_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((after_daily->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_after_weekly_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((after_weekly->>'rank')::integer),strategy_id);
CREATE INDEX IF NOT EXISTS ix_fl_after_monthly_rank ON flow_v3_leadership_snapshot
 (snapshot_date,capital_base,((after_monthly->>'rank')::integer),strategy_id);
COMMENT ON TABLE flow_v3_leadership_snapshot IS
 'Independent LONG UNDERLYING research. Period JSON columns; no source trading FK/cascade. Missing data is NULL, never zero return.';
COMMIT;
