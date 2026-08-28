-- Deterministic TEST-only durable lifecycle.  Entire fixture is rolled back.
BEGIN;

CREATE TEMP TABLE tmp_v1_paths AS
SELECT pp.minute_policy_path_id,op.direction
FROM minute_ma_policy_path pp JOIN minute_ma_operation_policy op USING(policy_code)
ORDER BY op.direction,pp.minute_policy_path_id;

CREATE TEMP TABLE tmp_v1_target AS
SELECT min(minute_policy_path_id) FILTER(WHERE direction='SHORT') short_path,
       min(minute_policy_path_id) FILTER(WHERE direction='LONG') long_path
FROM tmp_v1_paths;

INSERT INTO minute_ma_policy_paper_event(minute_policy_path_id,signal_event_key,event_type,
 source_bar_time,confirmed_at,proxy_bar_time,proxy_price,underlying_price,source_snapshot)
SELECT short_path,repeat('a',64),'ENTRY',TIMESTAMP '2026-08-27 09:00',TIMESTAMP '2026-08-27 09:01:01',
 TIMESTAMP '2026-08-27 09:01',90,100,'{"fixture":"V1_E2E"}'::jsonb FROM tmp_v1_target
UNION ALL SELECT short_path,repeat('b',64),'ENTRY',TIMESTAMP '2026-08-27 09:01',TIMESTAMP '2026-08-27 09:02:01',
 TIMESTAMP '2026-08-27 09:02',91,110,'{"fixture":"V1_E2E"}'::jsonb FROM tmp_v1_target
UNION ALL SELECT long_path,repeat('c',64),'ENTRY',TIMESTAMP '2026-08-27 15:18',TIMESTAMP '2026-08-27 15:19:01',
 TIMESTAMP '2026-08-27 15:19',100,100,'{"fixture":"V1_E2E"}'::jsonb FROM tmp_v1_target;

INSERT INTO minute_ma_policy_paper_trade(minute_policy_path_id,entry_event_key,trade_status,
 entry_signal_time,entry_execution_time,entry_price,underlying_entry_reference_price,
 stop_threshold_price,stop_policy,basis_capital)
SELECT short_path,repeat('a',64),'OPEN',TIMESTAMP '2026-08-27 09:01:01',TIMESTAMP '2026-08-27 09:01',90,100,101,'UNDERLYING_1PCT',1000000 FROM tmp_v1_target
UNION ALL SELECT short_path,repeat('b',64),'OPEN',TIMESTAMP '2026-08-27 09:02:01',TIMESTAMP '2026-08-27 09:02',91,110,111.1,'UNDERLYING_1PCT',1000000 FROM tmp_v1_target
UNION ALL SELECT long_path,repeat('c',64),'OPEN',TIMESTAMP '2026-08-27 15:19:01',TIMESTAMP '2026-08-27 15:19',100,100,95,'UNDERLYING_5PCT',1000000 FROM tmp_v1_target;

CREATE TEMP TABLE tmp_v1_close AS
SELECT minute_policy_paper_trade_id,minute_policy_path_id,entry_price,basis_capital,
 CASE entry_event_key WHEN repeat('a',64) THEN 89::numeric ELSE 94::numeric END exit_price,
 CASE entry_event_key WHEN repeat('a',64) THEN TIMESTAMP '2026-08-28 09:01' ELSE TIMESTAMP '2026-08-28 09:01' END exit_time,
 CASE entry_event_key WHEN repeat('a',64) THEN 102::numeric ELSE 94::numeric END trigger_close
FROM minute_ma_policy_paper_trade WHERE entry_event_key IN(repeat('a',64),repeat('c',64));

UPDATE minute_ma_policy_paper_trade t SET trade_status='CLOSED',exit_reason='STOP_EXIT',
 exit_signal_time=x.exit_time+INTERVAL '1 second',exit_execution_time=x.exit_time,
 exit_price=x.exit_price,stop_trigger_time=x.exit_time-INTERVAL '1 minute',
 stop_trigger_underlying_close=x.trigger_close,
 gross_return_pct=(x.exit_price/x.entry_price-1)*100,
 net_return_pct=(x.exit_price/x.entry_price-1)*100-0.20,
 realized_pnl=x.basis_capital*((x.exit_price/x.entry_price-1)*100-0.20)/100
FROM tmp_v1_close x WHERE t.minute_policy_paper_trade_id=x.minute_policy_paper_trade_id;

INSERT INTO minute_ma_policy_paper_settlement(minute_policy_paper_trade_id,minute_policy_path_id,
 realized_pnl,capital_after)
SELECT x.minute_policy_paper_trade_id,x.minute_policy_path_id,t.realized_pnl,
       c.current_capital+t.realized_pnl
FROM tmp_v1_close x JOIN minute_ma_policy_paper_trade t
  ON t.minute_policy_paper_trade_id=x.minute_policy_paper_trade_id
JOIN minute_ma_policy_paper_capital c ON c.minute_policy_path_id=x.minute_policy_path_id
ON CONFLICT DO NOTHING;

UPDATE minute_ma_policy_paper_capital c SET current_capital=c.current_capital+s.realized_pnl,
 cumulative_realized_pnl=c.cumulative_realized_pnl+s.realized_pnl,version=version+1
FROM minute_ma_policy_paper_settlement s JOIN tmp_v1_close x USING(minute_policy_paper_trade_id)
WHERE c.minute_policy_path_id=s.minute_policy_path_id;

-- Restart/same event: the settlement identity prevents another write.
INSERT INTO minute_ma_policy_paper_settlement(minute_policy_paper_trade_id,minute_policy_path_id,
 realized_pnl,capital_after)
SELECT x.minute_policy_paper_trade_id,x.minute_policy_path_id,t.realized_pnl,
       c.current_capital+t.realized_pnl
FROM tmp_v1_close x JOIN minute_ma_policy_paper_trade t
  ON t.minute_policy_paper_trade_id=x.minute_policy_paper_trade_id
JOIN minute_ma_policy_paper_capital c ON c.minute_policy_path_id=x.minute_policy_path_id
ON CONFLICT DO NOTHING;

DO $$ DECLARE settled bigint; open_short bigint; invariant bigint; versions integer; BEGIN
 SELECT count(*) INTO settled FROM minute_ma_policy_paper_settlement s
 JOIN tmp_v1_close x USING(minute_policy_paper_trade_id);
 SELECT count(*) INTO open_short FROM minute_ma_policy_paper_trade t JOIN tmp_v1_target x
 ON t.minute_policy_path_id=x.short_path WHERE t.trade_status='OPEN';
 SELECT count(*) INTO invariant FROM minute_ma_policy_paper_capital
 WHERE current_capital<>initial_capital+cumulative_realized_pnl;
 SELECT sum(version) INTO versions FROM minute_ma_policy_paper_capital c
 JOIN (SELECT DISTINCT minute_policy_path_id FROM tmp_v1_close) x USING(minute_policy_path_id);
 IF settled<>2 OR open_short<>1 OR invariant<>0 OR versions<>2 THEN
   RAISE EXCEPTION 'V1 durable fixture failed: settlements %, open short %, invariant %, versions %',
     settled,open_short,invariant,versions;
 END IF;
END $$;

--name: minute_ma_v10_durable_fixture_result
SELECT 2 AS exactly_once_settlements,1 AS independently_open_short_trade,
       1 AS overnight_long_gap_stop,0 AS duplicate_settlement,0 AS capital_invariant_error,
       0 AS broker_order_post;

ROLLBACK;
