"""Read-only first LIVE operation preview; it intentionally writes nothing."""
from __future__ import annotations
import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
def main():
 load_dotenv(ROOT/'.env');s=DatabaseSettings.from_environment();import psycopg
 with psycopg.connect(**s.connection_kwargs()) as c,c.cursor() as q:
  q.execute("""SELECT s.strategy_id,s.selection_tier,s.recommended_amount,o.operation_status,o.allocated_amount,
                     'LIVE'::text AS proposed_operation_status,s.recommended_amount AS proposed_initial_capital,
                     CASE WHEN o.capital_epoch_no<1 THEN 1 ELSE o.capital_epoch_no+1 END AS proposed_capital_epoch_no
                FROM daily_strategy_selection_snapshot s
                JOIN daily_strategy_selection_batch b USING(selection_batch_id)
                JOIN daily_strategy_operation o ON o.strategy_id=s.strategy_id AND o.effective_to IS NULL
               WHERE b.selection_batch_id='DAILY_MA_SEL_20260824_V1' AND b.status='APPROVED'
                 AND s.decision_status='SELECTED'
               ORDER BY CASE s.selection_tier WHEN 'CORE' THEN 1 WHEN 'ACTIVE' THEN 2 ELSE 3 END,s.strategy_id""")
  rows=[dict(zip(['strategy_id','selection_tier','recommended_amount','current_operation_status','current_allocated_amount','proposed_operation_status','proposed_initial_capital','proposed_capital_epoch_no'],r)) for r in q.fetchall()]
 if os.getenv('SUMMARY_ONLY')=='YES':
  print(json.dumps({'preview_only':True,'selected_count':len(rows),
   'core':sum(r['selection_tier']=='CORE' for r in rows),'active':sum(r['selection_tier']=='ACTIVE' for r in rows),
   'observe':sum(r['selection_tier']=='OBSERVE' for r in rows),'current_live':sum(r['current_operation_status']=='LIVE' for r in rows),
   'proposed_initial_capital_total':str(sum((r['proposed_initial_capital'] or 0) for r in rows))},default=str))
 else: print(json.dumps({'preview_only':True,'selected_count':len(rows),'rows':rows},default=str))
if __name__=='__main__':raise SystemExit(main())
