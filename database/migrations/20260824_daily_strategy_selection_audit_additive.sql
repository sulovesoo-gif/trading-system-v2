-- Daily MA Selection Audit. Additive; does not change operation or capital.
BEGIN;
CREATE TABLE IF NOT EXISTS daily_strategy_selection_batch (
  selection_batch_id VARCHAR(64) PRIMARY KEY,
  selected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  evaluation_cutoff_date DATE NOT NULL,
  metric_contract_version VARCHAR(64) NOT NULL,
  description TEXT NOT NULL,
  status VARCHAR(16) NOT NULL CHECK (status IN ('DRAFT','APPROVED','SUPERSEDED')),
  created_by VARCHAR(64) NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_strategy_selection_snapshot (
  selection_batch_id VARCHAR(64) NOT NULL REFERENCES daily_strategy_selection_batch(selection_batch_id),
  strategy_id VARCHAR(20) NOT NULL REFERENCES daily_strategy_master(strategy_id),
  evaluation_rank INTEGER NOT NULL CHECK (evaluation_rank > 0),
  decision_status VARCHAR(16) NOT NULL CHECK (decision_status IN ('SELECTED','NOT_SELECTED')),
  selection_tier VARCHAR(16) NOT NULL CHECK (selection_tier IN ('CORE','ACTIVE','OBSERVE','NONE')),
  recommended_amount NUMERIC(18,2), approved_amount NUMERIC(18,2),
  historical_completed_trade_count INTEGER NOT NULL, historical_compound_return_pct NUMERIC(20,8),
  historical_compound_profit NUMERIC(20,2), historical_win_rate NUMERIC(10,4), historical_provenance TEXT[] NOT NULL,
  actual_completed_trade_count INTEGER NOT NULL, actual_compound_return_pct NUMERIC(20,8),
  actual_compound_profit NUMERIC(20,2), actual_win_rate NUMERIC(10,4),
  aug_completed_trade_count INTEGER NOT NULL, aug_compound_return_pct NUMERIC(20,8),
  aug_compound_profit NUMERIC(20,2), aug_win_rate NUMERIC(10,4),
  criterion_1 BOOLEAN NOT NULL, criterion_2 BOOLEAN NOT NULL, criterion_3 BOOLEAN NOT NULL, criterion_4 BOOLEAN NOT NULL,
  strong_recommendation BOOLEAN NOT NULL DEFAULT FALSE, reason_codes TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
  reason_text TEXT NOT NULL, manual_override_yn BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (selection_batch_id,strategy_id),
  CHECK ((decision_status='SELECTED') = (selection_tier <> 'NONE')),
  CHECK ((selection_tier='NONE') = (recommended_amount IS NULL)),
  CHECK (approved_amount IS NULL OR approved_amount >= 0)
);
CREATE INDEX IF NOT EXISTS ix_daily_strategy_selection_snapshot_current
 ON daily_strategy_selection_snapshot(selection_batch_id,selection_tier,evaluation_rank);
CREATE OR REPLACE FUNCTION fn_daily_strategy_selection_snapshot_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'selection snapshot is immutable; create a new batch'; END $$;
DROP TRIGGER IF EXISTS trg_daily_strategy_selection_snapshot_immutable ON daily_strategy_selection_snapshot;
CREATE TRIGGER trg_daily_strategy_selection_snapshot_immutable BEFORE UPDATE OR DELETE ON daily_strategy_selection_snapshot
 FOR EACH ROW EXECUTE FUNCTION fn_daily_strategy_selection_snapshot_immutable();

CREATE OR REPLACE VIEW vw_daily_strategy_selection_dashboard AS
WITH approved AS (
 SELECT selection_batch_id FROM daily_strategy_selection_batch WHERE status='APPROVED'
 ORDER BY selected_at DESC, selection_batch_id DESC LIMIT 1
), actual AS (
 SELECT p.strategy_id,count(*)::int AS n,
        (exp(sum(ln(1+p.return_pct/100.0)))-1)*100 AS compound_return,
        ((exp(sum(ln(1+p.return_pct/100.0)))-1)*1000000) AS compound_profit,
        avg((p.return_pct>0)::int)*100 AS win_rate,
        max(p.entry_signal_date) AS latest_closed_date,
        count(*) FILTER (WHERE p.entry_signal_date >= current_date-29)::int AS trailing_30d_closed_count,
        count(*) FILTER (WHERE p.entry_signal_date >= current_date-6)::int AS trailing_7d_closed_count,
        count(*) FILTER (WHERE p.entry_signal_date=current_date)::int AS today_closed_count
 FROM daily_strategy_paper_trade p JOIN daily_strategy_master m USING(strategy_id)
 WHERE m.strategy_role='CANONICAL' AND m.is_enabled='Y' AND p.trade_status='CLOSED' AND p.return_pct IS NOT NULL
   AND p.entry_signal_date BETWEEN DATE '2026-05-27' AND DATE '2026-08-21' AND p.data_segment='POST_LISTING_ACTUAL'
   AND COALESCE(p.source_system,'') NOT LIKE '%TEST%'
 GROUP BY p.strategy_id
), aug AS (
 SELECT p.strategy_id,count(*)::int AS n,(exp(sum(ln(1+p.return_pct/100.0)))-1)*100 AS compound_return,
        avg((p.return_pct>0)::int)*100 AS win_rate
 FROM daily_strategy_paper_trade p JOIN daily_strategy_master m USING(strategy_id)
 WHERE m.strategy_role='CANONICAL' AND m.is_enabled='Y' AND p.trade_status='CLOSED' AND p.return_pct IS NOT NULL
   AND p.entry_signal_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-21' AND p.data_segment='POST_LISTING_ACTUAL'
   AND COALESCE(p.source_system,'') NOT LIKE '%TEST%'
 GROUP BY p.strategy_id
)
SELECT m.strategy_id,m.strategy_name,m.signal_code,m.execution_code,m.direction,m.entry_fast_ma,m.entry_slow_ma,
 m.exit_fast_ma,m.exit_slow_ma,m.trend_ma,s.decision_status,s.selection_tier,s.recommended_amount,s.approved_amount,
 o.operation_status,o.allocated_amount,COALESCE(a.n,0) AS actual_completed_trade_count,a.compound_return AS actual_compound_return_pct,
 a.compound_profit AS actual_compound_profit,a.win_rate AS actual_win_rate,COALESCE(g.n,0) AS aug_completed_trade_count,
 g.compound_return AS aug_compound_return_pct,g.win_rate AS aug_win_rate,a.latest_closed_date,
 COALESCE(a.trailing_30d_closed_count,0) AS trailing_30d_closed_count,COALESCE(a.trailing_7d_closed_count,0) AS trailing_7d_closed_count,
 COALESCE(a.today_closed_count,0) AS today_closed_count
FROM daily_strategy_master m JOIN daily_strategy_operation o ON o.strategy_id=m.strategy_id AND o.effective_to IS NULL
LEFT JOIN approved b ON TRUE LEFT JOIN daily_strategy_selection_snapshot s ON s.selection_batch_id=b.selection_batch_id AND s.strategy_id=m.strategy_id
LEFT JOIN actual a ON a.strategy_id=m.strategy_id LEFT JOIN aug g ON g.strategy_id=m.strategy_id
WHERE m.strategy_role='CANONICAL' AND m.is_enabled='Y';
COMMIT;
