-- READ ONLY. 10% cap-hit is a separate cohort; other buckets use actual participation.
WITH samples AS (
 SELECT *, COALESCE(live_return_final,live_return_provisional) AS live_return,
   (evidence->>'entry_actual_participation_pct')::numeric AS participation,
   (evidence->>'return_gap_bp')::numeric AS gap,
   (evidence->>'entry_slippage_bps_vs_shadow')::numeric AS entry_slippage,
   (evidence->>'exit_slippage_bps_vs_shadow')::numeric AS exit_slippage
 FROM first_rise_j_capacity_observation
 WHERE comparison_status IN ('PROVISIONAL','FINAL') AND shadow_net_return IS NOT NULL
), grouped AS (
 SELECT *, CASE WHEN (evidence->>'liquidity_cap_hit')::boolean THEN '10% cap hit'
   WHEN participation<1 THEN '0-1%' WHEN participation<2 THEN '1-2%'
   WHEN participation<5 THEN '2-5%' WHEN participation<7.5 THEN '5-7.5%'
   WHEN participation<=10 THEN '7.5-10%' ELSE '>10% actual (slippage/market move)' END AS bucket
 FROM samples
)
SELECT bucket,exit_reason='STOP_ENTRY_BREAK' AS stop_entry_break,count(*) AS matched_trades,
 avg(shadow_net_return) AS shadow_avg_return,avg(live_return) AS live_avg_return,
 CASE WHEN avg(shadow_net_return)>0 THEN (avg(shadow_net_return)-avg(live_return))/abs(avg(shadow_net_return))*100 END AS degradation_pct,
 avg(gap) AS avg_return_gap_bp,percentile_cont(.5) WITHIN GROUP(ORDER BY gap) AS median_return_gap_bp,
 avg(entry_slippage) AS entry_slippage_bps,avg(exit_slippage) AS exit_slippage_bps,
 avg(CASE WHEN (evidence->>'buy_partial_fill_yn')::boolean OR (evidence->>'sell_partial_fill_yn')::boolean THEN 1.0 ELSE 0 END) AS observed_partial_fill_rate,
 avg((evidence->>'buy_fill_duration_ms')::numeric) AS buy_completion_observed_ms,
 avg((evidence->>'sell_fill_duration_ms')::numeric) AS sell_completion_observed_ms
FROM grouped GROUP BY bucket,exit_reason='STOP_ENTRY_BREAK' ORDER BY bucket,stop_entry_break;

SELECT window_size,severity,revision,evidence,updated_at FROM first_rise_capacity_warning ORDER BY window_size;
SELECT event_id,window_size,revision,delivery_status,created_at FROM first_rise_capacity_alert ORDER BY event_id DESC LIMIT 20;
