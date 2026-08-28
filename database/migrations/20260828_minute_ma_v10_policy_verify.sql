--name: minute_ma_v10_policy_verification
SELECT
 (SELECT count(*) FROM minute_ma_strategy_master WHERE is_enabled='Y') strategy_count,
 (SELECT count(*) FROM minute_ma_path WHERE is_enabled='Y') legacy_path_count,
 (SELECT count(*) FROM minute_ma_policy_path WHERE is_enabled='Y') v1_policy_path_count,
 (SELECT count(*) FROM minute_ma_v1_candidate_plan) candidate_count,
 (SELECT sum(proposed_initial_capital) FROM minute_ma_v1_candidate_plan) proposed_capital,
 (SELECT count(*) FROM minute_ma_operation WHERE effective_to IS NULL AND operation_status='LIVE') legacy_live_count,
 (SELECT count(*) FROM minute_ma_policy_operation WHERE effective_to IS NULL AND operation_status='LIVE') v1_live_count,
 (SELECT count(*) FROM minute_ma_policy_operation WHERE effective_to IS NULL AND operation_status='PAPER') v1_paper_count,
 (SELECT count(*) FROM minute_ma_policy_compound_capital) v1_capital_epoch_count,
 (SELECT count(*) FROM vw_minute_ma_v1_current_selection) v1_selection_count,
 (SELECT count(*) FROM minute_ma_compound_capital) legacy_capital_epoch_count,
 (SELECT send_enabled FROM minute_ma_send_profile WHERE profile_code='MINUTE_MA_LIVE_SEND') send_enabled;

--name: minute_ma_v10_policy_contract
SELECT policy_code,direction,paper_entry_start,paper_entry_end,live_entry_start,live_entry_end,
       holding_policy,stop_policy,stop_percent,stop_direction
FROM minute_ma_operation_policy ORDER BY direction;

--name: minute_ma_v10_duplicate_orphan
SELECT
 (SELECT count(*)-count(DISTINCT minute_policy_path_id) FROM minute_ma_policy_path) duplicate_policy_path,
 (SELECT count(*) FROM minute_ma_policy_path pp LEFT JOIN minute_ma_path p USING(minute_path_id)
   WHERE p.minute_path_id IS NULL) orphan_policy_path,
 (SELECT count(*) FROM minute_ma_policy_paper_trade t LEFT JOIN minute_ma_policy_path p USING(minute_policy_path_id)
   WHERE p.minute_policy_path_id IS NULL) orphan_trade,
 (SELECT count(*) FROM minute_ma_policy_paper_settlement s LEFT JOIN minute_ma_policy_paper_trade t USING(minute_policy_paper_trade_id)
   WHERE t.minute_policy_paper_trade_id IS NULL) orphan_settlement,
 (SELECT count(*) FROM minute_ma_policy_paper_capital
   WHERE current_capital<>initial_capital+cumulative_realized_pnl) paper_capital_invariant_error,
 (SELECT count(*) FROM minute_ma_policy_compound_capital
   WHERE strategy_compound_capital<>epoch_initial_capital+cumulative_net_realized_pnl) live_capital_invariant_error,
 (SELECT count(*) FROM minute_ma_policy_operation o LEFT JOIN minute_ma_policy_path p USING(minute_policy_path_id)
   WHERE p.minute_policy_path_id IS NULL) orphan_policy_operation,
 (SELECT count(*) FROM minute_ma_policy_compound_capital c LEFT JOIN minute_ma_policy_operation o
   ON o.minute_policy_operation_id=c.source_policy_operation_id
   WHERE o.minute_policy_operation_id IS NULL) orphan_policy_capital;

--name: minute_ma_v10_broker_write_guard
SELECT
 (SELECT count(*) FROM minute_ma_live_order_link) minute_order_mapping_count,
 (SELECT count(*) FROM minute_ma_live_fill_checkpoint) minute_fill_checkpoint_count,
 (SELECT count(*) FROM minute_ma_live_checkpoint_allocation) minute_fill_allocation_count;
