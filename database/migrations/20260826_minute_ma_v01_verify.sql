-- Read-only verification for the four-axis minute MA additive layer.
SELECT count(*) AS minute_strategy_count FROM minute_ma_strategy_master WHERE is_enabled='Y';
SELECT data_axis,count(*) AS path_count
  FROM minute_ma_path WHERE is_enabled='Y' GROUP BY data_axis ORDER BY data_axis;
SELECT operation_status,count(*) AS current_operation_count
  FROM minute_ma_operation WHERE effective_to IS NULL GROUP BY operation_status ORDER BY operation_status;
SELECT count(*) AS dashboard_rows,
       count(DISTINCT minute_path_id) AS dashboard_distinct_paths
  FROM vw_minute_ma_dashboard;
SELECT count(*) AS research_802_count FROM research_strategy_master;
SELECT (SELECT count(*) FROM daily_strategy_master) AS daily_master_count,
       (SELECT count(*) FROM daily_strategy_paper_trade) AS daily_paper_count,
       (SELECT count(*) FROM daily_strategy_live_trade) AS daily_live_count;
SELECT send_enabled AS minute_actual_send_enabled
  FROM minute_ma_send_profile WHERE profile_code='MINUTE_MA_LIVE_SEND';
SELECT count(*) AS paper_capital_rows,
       count(*) FILTER (WHERE current_capital<>initial_capital+cumulative_realized_pnl)
         AS paper_capital_invariant_errors
  FROM minute_ma_paper_capital;
SELECT decision_status,count(*) FROM vw_minute_ma_current_selection
 GROUP BY decision_status ORDER BY decision_status;
SELECT count(*) AS duplicate_events
  FROM (SELECT minute_path_id,signal_event_key,event_type,count(*)
          FROM minute_ma_paper_event GROUP BY 1,2,3 HAVING count(*)>1) d;
SELECT count(*) AS duplicate_trades
  FROM (SELECT minute_path_id,entry_event_key,count(*)
          FROM minute_ma_paper_trade GROUP BY 1,2 HAVING count(*)>1) d;
SELECT count(*) AS orphan_paths
  FROM minute_ma_path p LEFT JOIN minute_ma_strategy_master s USING(minute_strategy_id)
 WHERE s.minute_strategy_id IS NULL;
SELECT count(*) AS orphan_live_order_links
  FROM minute_ma_live_order_link l
  LEFT JOIN live_order_request r ON r.order_request_id=l.order_request_id
 WHERE r.order_request_id IS NULL;
SELECT count(*) AS duplicate_live_intents
  FROM (SELECT intent_key,count(*) FROM minute_ma_live_intent GROUP BY 1 HAVING count(*)>1) d;
SELECT count(*) AS out_of_contract_raw_references
  FROM minute_ma_paper_event e JOIN minute_ma_path p USING(minute_path_id)
 WHERE (p.market_source='KRX' AND e.source_bar_time::time NOT BETWEEN TIME '09:00' AND TIME '15:30')
    OR (p.market_source='INTEGRATED' AND e.source_bar_time::time NOT BETWEEN TIME '08:00' AND TIME '19:59');
