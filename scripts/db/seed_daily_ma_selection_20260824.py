"""Create the immutable first Daily MA selection snapshot on TEST only."""
from __future__ import annotations
import math, os, sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings

BATCH='DAILY_MA_SEL_20260824_V1'
CORE={'DS000103','DS000055','DS000487'}
ACTIVE={'DS000056','DS000104','DS000105','DS000225','DS000271','DS000272','DS000491','DS000538','DS001066','DS001087','DS001088'}
METRIC_SQL="""
SELECT p.strategy_id,count(*)::int,
 (exp(sum(ln(1+p.return_pct/100.0)))-1)*100,
 avg((p.return_pct>0)::int)*100,
 array_agg(DISTINCT CASE WHEN p.data_segment IN ('SYNTHETIC','POST_LISTING_ACTUAL','HYBRID') THEN p.data_segment ELSE 'HYBRID' END)
FROM daily_strategy_paper_trade p JOIN daily_strategy_master m USING(strategy_id)
WHERE m.strategy_role='CANONICAL' AND m.is_enabled='Y' AND p.trade_status='CLOSED' AND p.return_pct IS NOT NULL
 AND p.entry_signal_date {window} AND COALESCE(p.source_system,'') NOT LIKE '%TEST%'
 {segment}
GROUP BY p.strategy_id
"""
def fetch(cur, window, segment=''):
 cur.execute(METRIC_SQL.format(window=window,segment=segment));return {str(r[0]):{'count':r[1],'ret':Decimal(str(r[2])),'win':Decimal(str(r[3])),'prov':list(r[4])} for r in cur.fetchall()}
def compound_profit(metric): return (metric['ret']*Decimal('10000')) if metric else None
def main():
 load_dotenv(ROOT/'.env')
 if os.getenv('APPLY_DAILY_MA_SELECTION_SNAPSHOT')!='YES':raise SystemExit('set APPLY_DAILY_MA_SELECTION_SNAPSHOT=YES')
 s=DatabaseSettings.from_environment()
 if s.name!='trading_system_v2_test':raise SystemExit('selection seed is restricted to trading_system_v2_test')
 import psycopg
 with psycopg.connect(**s.connection_kwargs()) as c:
  with c.cursor() as q:
   q.execute('BEGIN')
   q.execute("SELECT strategy_id FROM daily_strategy_master WHERE strategy_role='CANONICAL' AND is_enabled='Y' ORDER BY strategy_id")
   strategies=[str(r[0]) for r in q.fetchall()]
   hist=fetch(q,"< DATE '2026-05-27'")
   actual=fetch(q,"BETWEEN DATE '2026-05-27' AND DATE '2026-08-21'","AND p.data_segment='POST_LISTING_ACTUAL'")
   aug=fetch(q,"BETWEEN DATE '2026-08-01' AND DATE '2026-08-21'","AND p.data_segment='POST_LISTING_ACTUAL'")
   selected={k for k,v in actual.items() if v['ret']>0 and v['win']>=50}
   if len(strategies)!=2400 or len(selected)!=346 or not (CORE|ACTIVE)<=selected or CORE&ACTIVE: raise RuntimeError('selection hard guard mismatch')
   q.execute("SELECT count(*) FROM daily_strategy_selection_batch WHERE selection_batch_id=%s",(BATCH,))
   if q.fetchone()[0]: raise RuntimeError('selection batch already exists')
   hist_top=sorted((compound_profit(v),k) for k,v in hist.items() if compound_profit(v) is not None)
   hist_top={k for _,k in hist_top[max(0,len(hist_top)-math.ceil(len(hist_top)*.1)): ]}
   current_top={k for _,k in sorted(((compound_profit(v),k) for k,v in actual.items()),reverse=True)[:10]}
   aug_top={k for _,k in sorted(((compound_profit(v),k) for k,v in aug.items()),reverse=True)[:10]}
   q.execute("""INSERT INTO daily_strategy_selection_batch(selection_batch_id,evaluation_cutoff_date,metric_contract_version,description,status,created_by)
                VALUES (%s,DATE '2026-08-21','DAILY_MA_SELECTION_V1','canonical 2400 immutable selection snapshot','DRAFT','codex')""",(BATCH,))
   ranked=sorted(strategies,key=lambda k:(actual.get(k,{}).get('ret',Decimal('-999999')),k),reverse=True)
   for rank,sid in enumerate(ranked,1):
    h,a,g=hist.get(sid),actual.get(sid),aug.get(sid)
    c1=bool(h and a and g and h['win']>=50 and a['win']>=50 and g['win']>=50)
    c2=bool(h and g and h['win']>=50 and g['win']>=50)
    c3=bool(h and a and h['win']>=50 and a['win']>=50)
    c4=sid in hist_top|current_top|aug_top
    if sid in CORE:tier,amount='CORE',Decimal('1000000')
    elif sid in ACTIVE:tier,amount='ACTIVE',Decimal('100000')
    elif sid in selected:tier,amount='OBSERVE',Decimal('30000')
    else:tier,amount='NONE',None
    decision='SELECTED' if sid in selected else 'NOT_SELECTED'
    reasons=[]
    if c1: reasons.append('THREE_PERIOD_WIN_GE_50')
    if c2: reasons.append('PAST_AUG_WIN_GE_50')
    if c3: reasons.append('PAST_RECENT_WIN_GE_50')
    if c4: reasons.append('HIGH_PROFIT_EXCEPTION')
    if sid in CORE: reasons.extend(['STRONG_RECOMMENDATION','MANUAL_RECOMMENDATION'])
    elif sid in ACTIVE: reasons.append('MANUAL_RECOMMENDATION')
    if not g: reasons.append('AUG_NO_COMPLETED_TRADE')
    if a and a['count']<3: reasons.append('LOW_SAMPLE')
    def vals(x): return (x['count'],x['ret'],compound_profit(x),x['win']) if x else (0,None,None,None)
    hv,av,gv=vals(h),vals(a),vals(g)
    q.execute("""INSERT INTO daily_strategy_selection_snapshot(
      selection_batch_id,strategy_id,evaluation_rank,decision_status,selection_tier,recommended_amount,approved_amount,
      historical_completed_trade_count,historical_compound_return_pct,historical_compound_profit,historical_win_rate,historical_provenance,
      actual_completed_trade_count,actual_compound_return_pct,actual_compound_profit,actual_win_rate,
      aug_completed_trade_count,aug_compound_return_pct,aug_compound_profit,aug_win_rate,
      criterion_1,criterion_2,criterion_3,criterion_4,strong_recommendation,reason_codes,reason_text,manual_override_yn)
      VALUES (%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
      (BATCH,sid,rank,decision,tier,amount,*hv,(h['prov'] if h else []),*av,*gv,c1,c2,c3,c4,sid in CORE,reasons,','.join(reasons) or 'NOT_SELECTED',sid in CORE|ACTIVE))
   q.execute("""SELECT count(*),count(*) FILTER(WHERE decision_status='SELECTED'),count(*) FILTER(WHERE decision_status='NOT_SELECTED'),
                    count(*) FILTER(WHERE selection_tier='CORE'),count(*) FILTER(WHERE selection_tier='ACTIVE'),
                    count(*) FILTER(WHERE selection_tier='OBSERVE'),count(*) FILTER(WHERE selection_tier='NONE'),
                    count(*) FILTER(WHERE strong_recommendation),count(*) FILTER(WHERE approved_amount IS NOT NULL)
                FROM daily_strategy_selection_snapshot WHERE selection_batch_id=%s""",(BATCH,))
   if q.fetchone() != (2400,346,2054,3,11,332,2054,3,0): raise RuntimeError('post-seed verification mismatch')
   q.execute("UPDATE daily_strategy_selection_batch SET status='APPROVED' WHERE selection_batch_id=%s AND status='DRAFT'",(BATCH,))
   if q.rowcount!=1:raise RuntimeError('approval state transition failed')
   c.commit()
 print('APPROVED',BATCH)
if __name__=='__main__':main()
