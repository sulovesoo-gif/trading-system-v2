-- Golden v1.1.0 full-universe generator.  Read-only RAW, append-only Golden.
-- S1 uses approved Variant B: post-OR breakout time > 09:30.

BEGIN;

INSERT INTO strategy_golden_artifact (
    golden_version, raw_period_start, raw_period_end, raw_cutoff_timestamp,
    signal_source_venue, historical_execution_rule, provenance_status, metadata
) VALUES (
    '1.1.0', DATE '2026-05-27', DATE '2026-08-14', CURRENT_TIMESTAMP,
    'INTEGRATED', 'KRX_EXACT_TIMESTAMP_ENTRY__NEXT_ACTUAL_KRX_BAR_EXIT', 'CANONICAL_COMPLETE_RAW',
    jsonb_build_object(
      's1_boundary', 'breakout_time > 09:30',
      'cost_policy', jsonb_build_object('etf_fee_rate', 0.000146527, 'slippage_rate', 0.0002, 'each_side', true, 'integer_quantity', true, 'independent_compound', true),
      'strategies', jsonb_build_object('S1_OR_PULLBACK_RESTART','1.1.0','S2_FAILED_OR_VWAP','1.1.0','S3_VOLUME_CLIMAX_REVERSAL','1.1.0'),
      'entry_parameters', jsonb_build_object(
        'S1_OR_PULLBACK_RESTART', jsonb_build_object('or_window','09:00<=t<09:30','breakout_boundary','t>09:30','pullback_window_minutes',30,'restart_window_minutes',20),
        'S2_FAILED_OR_VWAP', jsonb_build_object('or_window','09:00<=t<09:30','breakout_boundary','t>09:30','signal_window_minutes',20),
        'S3_VOLUME_CLIMAX_REVERSAL', jsonb_build_object('return_bars',5,'return_threshold',0.008,'rvol_bars',20,'rvol_threshold',2.0,'confirm_window_minutes',8)
      ),
      'exit_parameters', jsonb_build_object(
        'S1_OR_PULLBACK_RESTART','PULLBACK_LOW_BREAK_WITHIN30_EOD',
        'S2_FAILED_OR_VWAP','FIXED_30',
        'HYNIX_S3_SHORT_3BAR','STRUCTURE_3BAR_MAX30_STOP_2.5',
        'HYNIX_S3_SHORT_5BAR','STRUCTURE_5BAR_MAX30_STOP_2.5'
      )
    )
);

WITH base_005930 AS (
 SELECT bar_time,bar_time::date d,bar_time::time t,open_price o,high_price h,low_price l,close_price c,volume v,
   lag(close_price) over w pc,lag(high_price) over w ph,lead(bar_time) over w nt,
   max(high_price) filter(where bar_time::time>=time '09:00' and bar_time::time<time '09:30') over(partition by bar_time::date) oh,
   min(low_price) filter(where bar_time::time>=time '09:00' and bar_time::time<time '09:30') over(partition by bar_time::date) ol,
   sum(((high_price+low_price+close_price)/3)*volume) over w / nullif(sum(volume) over w,0) vwap
 FROM raw_stock_minute WHERE stock_code='005930' AND trading_venue='INTEGRATED' AND collect_cycle='1MIN'
   AND bar_time::date between date '2026-05-27' and date '2026-08-14' and bar_time::time between time '09:00' and time '15:19'
 WINDOW w AS(partition by bar_time::date order by bar_time)
), days AS (SELECT DISTINCT d FROM base_005930),
s1_bo AS (SELECT d.d,x.bar_time bt,x.v bv,x.oh,x.ol FROM days d join lateral(
 SELECT * FROM base_005930 where base_005930.d=d.d and t>time '09:30' and c>oh and coalesce(pc,oh)<=oh and c>o and abs(c-o)/nullif(h-l,0)>=.5 order by bar_time limit 1)x on true),
s1_pb AS (SELECT b.*,x.bar_time pt,x.h phigh,x.l plow,x.v pv FROM s1_bo b cross join lateral(
 SELECT * FROM base_005930 where d=b.d and bar_time>b.bt and bar_time<=b.bt+interval '30 min' and l<=oh*1.003 and c>=oh*.997 and v<=b.bv order by bar_time limit 1)x),
s1_signal AS (SELECT p.*,x.bar_time st,x.nt et FROM s1_pb p cross join lateral(
 SELECT * FROM base_005930 where d=p.d and bar_time>p.pt and bar_time<=p.pt+interval '20 min' and c>o and c>ph and c>oh and abs(c-o)/nullif(h-l,0)>=.5 and v>=p.pv*1.1 order by bar_time limit 1)x),
s1_entry AS (SELECT s.*,e.open_price entry_raw FROM s1_signal s join raw_stock_minute e on e.stock_code='0193W0' and e.trading_venue='KRX' and e.collect_cycle='1MIN' and e.bar_time=s.et),
s1_resolved AS (SELECT e.*,(select min(b.bar_time) from base_005930 b where b.d=e.d and b.bar_time>=e.et and b.bar_time<=e.et+interval '30 min' and b.t<=time '15:18' and b.c<e.plow) stop_trigger FROM s1_entry e),
s1_rows AS (SELECT *,case when stop_trigger is null then d+time '15:19' else (select min(x.bar_time) from raw_stock_minute x where x.stock_code='0193W0' and x.trading_venue='KRX' and x.collect_cycle='1MIN' and x.bar_time>stop_trigger) end xt,case when stop_trigger is null then 'EOD_1519' else 'PULLBACK_LOW_BREAK_WITHIN30' end xr FROM s1_resolved),
s2_bo AS (SELECT d.d,x.bar_time bt FROM days d join lateral(select * from base_005930 where base_005930.d=d.d and t>time '09:30' and h>oh and c>=oh order by bar_time limit 1)x on true),
s2_signal AS (SELECT b.*,x.bar_time st,x.nt et,x.oh,x.vwap FROM s2_bo b cross join lateral(select * from base_005930 where d=b.d and bar_time>b.bt and bar_time<=b.bt+interval '20 min' and c<oh and c<o and c>vwap order by bar_time limit 1)x),
s2_rows AS (SELECT s.*,e.open_price entry_raw,s.et+interval '30 min' xt FROM s2_signal s join raw_stock_minute e on e.stock_code='0193L0' and e.trading_venue='KRX' and e.collect_cycle='1MIN' and e.bar_time=s.et),
base_000660 AS (
 SELECT bar_time,bar_time::date d,bar_time::time t,open_price o,high_price h,low_price l,close_price c,volume v,lag(close_price,5) over w c5,lead(bar_time) over w nt,avg(volume) over(partition by bar_time::date order by bar_time rows between 20 preceding and 1 preceding) vavg
 FROM raw_stock_minute WHERE stock_code='000660' AND trading_venue='INTEGRATED' AND collect_cycle='1MIN' AND bar_time::date between date '2026-05-27' and date '2026-08-14' and bar_time::time between time '09:00' and time '15:19' WINDOW w AS(partition by bar_time::date order by bar_time)
), s3_c0 AS (SELECT *,row_number() over(partition by d order by bar_time) rn FROM base_000660 where t between time '09:10' and time '14:50' and c/c5-1>=.008 and v/nullif(vavg,0)>=2 and (h-greatest(o,c))/nullif(abs(c-o),0)>=.5),
s3_signal AS (SELECT cl.*,x.bar_time st,x.nt et FROM s3_c0 cl cross join lateral(select f.* from base_000660 f where f.d=cl.d and f.bar_time>cl.bar_time and f.bar_time<=cl.bar_time+interval '8 min' and f.h<=cl.h and f.c<least(cl.o,cl.c) and f.c<f.o order by f.bar_time limit 1)x where cl.rn=1),
s3_entry AS (SELECT s.*,e.open_price entry_raw FROM s3_signal s join raw_stock_minute e on e.stock_code='0197X0' and e.trading_venue='KRX' and e.collect_cycle='1MIN' and e.bar_time=s.et),
s3_variants(n,inst) AS(values(3,'HYNIX_S3_SHORT_3BAR'),(5,'HYNIX_S3_SHORT_5BAR')),
s3_rows AS (SELECT v.*,e.*,(select min(b.bar_time) from base_000660 b where b.d=e.d and b.bar_time>=e.et+interval '5 min' and b.c>(select max(z.h) from base_000660 z where z.d=e.d and z.bar_time<b.bar_time and z.bar_time>=b.bar_time-(v.n||' min')::interval)) struct_trigger,(select min(x.bar_time) from raw_stock_minute x where x.stock_code='0197X0' and x.trading_venue='KRX' and x.collect_cycle='1MIN' and x.bar_time>(select min(b.bar_time) from base_000660 b where b.d=e.d and b.bar_time>=e.et+interval '5 min' and b.c>(select max(z.h) from base_000660 z where z.d=e.d and z.bar_time<b.bar_time and z.bar_time>=b.bar_time-(v.n||' min')::interval))) struct_exec,(select min(x.bar_time) from raw_stock_minute x where x.stock_code='0197X0' and x.trading_venue='KRX' and x.collect_cycle='1MIN' and x.bar_time>(select min(q.bar_time) from raw_stock_minute q where q.stock_code='0197X0' and q.trading_venue='KRX' and q.collect_cycle='1MIN' and q.bar_time>=e.et and q.close_price<=e.entry_raw*.975)) stop_exec FROM s3_variants v cross join s3_entry e),
all_rows AS (
 SELECT 'SAMSUNG_S1_LONG_PULLBACK_WITHIN30_EOD' inst,'S1_OR_PULLBACK_RESTART' code,d,'005930' ss,'LONG' sd,'0193W0' es,'LONG' ed,st,et,et entryexec,coalesce(stop_trigger,d+time '15:19') exittrigger,xt exitexec,entry_raw,(select case when xr='EOD_1519' then close_price else open_price end from raw_stock_minute where stock_code='0193W0' and trading_venue='KRX' and collect_cycle='1MIN' and bar_time=xt) exitraw,xr exitreason,null::text shared,jsonb_build_object('or_high',oh,'or_low',ol,'breakout_time',bt,'pullback_time',pt,'pullback_low',plow) ref FROM s1_rows
 UNION ALL SELECT 'SAMSUNG_S2_SHORT_FIXED30','S2_FAILED_OR_VWAP',d,'005930','SHORT','0193L0','LONG',st,et,et,xt,xt,entry_raw,(select open_price from raw_stock_minute where stock_code='0193L0' and trading_venue='KRX' and collect_cycle='1MIN' and bar_time=xt),'FIXED_30',null,jsonb_build_object('or_high',oh,'vwap',vwap,'breakout_time',bt) FROM s2_rows
 UNION ALL SELECT inst,'S3_VOLUME_CLIMAX_REVERSAL',d,'000660','SHORT','0197X0','LONG',st,et,et,case when struct_exec is not null and (stop_exec is null or struct_exec<=stop_exec) and struct_exec<=et+interval '30 min' then struct_exec-interval '1 min' when stop_exec is not null and stop_exec<et+interval '30 min' then stop_exec-interval '1 min' else et+interval '30 min' end,case when struct_exec is not null and (stop_exec is null or struct_exec<=stop_exec) and struct_exec<=et+interval '30 min' then struct_exec when stop_exec is not null and stop_exec<et+interval '30 min' then stop_exec else et+interval '30 min' end,entry_raw,(select open_price from raw_stock_minute where stock_code='0197X0' and trading_venue='KRX' and collect_cycle='1MIN' and bar_time=(case when struct_exec is not null and (stop_exec is null or struct_exec<=stop_exec) and struct_exec<=et+interval '30 min' then struct_exec when stop_exec is not null and stop_exec<et+interval '30 min' then stop_exec else et+interval '30 min' end)),case when struct_exec is not null and (stop_exec is null or struct_exec<=stop_exec) and struct_exec<=et+interval '30 min' then 'STRUCTURE_RECLAIM' when stop_exec is not null and stop_exec<et+interval '30 min' then 'STOP_2.5' else 'MAX_30' end,'HYNIX_S3_'||to_char(d,'YYYYMMDD')||'_'||to_char(st,'HH24MI'),jsonb_build_object('structure_bars',n,'climax_time',bar_time,'ret5bar',c/c5-1,'rvol20',v/nullif(vavg,0),'climax_high',h) FROM s3_rows
)
INSERT INTO strategy_golden_row SELECT '1.1.0',inst,code,'1.1.0',d,ss,sd,es,ed,st,et,entryexec,exittrigger,exitexec,entry_raw,exitraw,exitreason,shared,ref,'COMPLETE_RAW_CANDIDATE_V1_1' FROM all_rows;

COMMIT;
