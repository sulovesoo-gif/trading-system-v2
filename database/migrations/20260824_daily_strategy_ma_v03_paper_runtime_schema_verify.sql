-- Read-only verification for Daily MA V0.3 PAPER Runtime additive schema.
-- Must be run after the schema migration, not before any runtime write.
WITH expected(name) AS (
    VALUES ('daily_strategy_trade_no_counter'),
           ('daily_strategy_paper_event'),
           ('daily_strategy_paper_transition'),
           ('daily_strategy_paper_runtime_cursor')
)
SELECT e.name, (c.oid IS NOT NULL) AS exists
  FROM expected e
  LEFT JOIN pg_class c ON c.relname=e.name AND c.relkind='r'
 ORDER BY e.name;

SELECT constraint_name, table_name
  FROM information_schema.table_constraints
 WHERE table_name IN ('daily_strategy_paper_event','daily_strategy_paper_transition')
 ORDER BY table_name, constraint_name;

SELECT count(*) AS canonical_runtime_strategies
  FROM vw_daily_strategy_v03_runtime;

SELECT count(*) AS v03_runtime_paper_rows
  FROM daily_strategy_paper_trade
 WHERE source_system='DAILY_MA_V03';

SELECT count(*) AS event_rows, count(*) FILTER (WHERE outcome='BLOCKED_INPUT_MISMATCH') AS blocked_input_mismatches
  FROM daily_strategy_paper_event;
