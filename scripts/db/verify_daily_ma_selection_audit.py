"""Read-only verification for the approved first Selection Audit batch."""
from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
def main():
 load_dotenv(ROOT/'.env');s=DatabaseSettings.from_environment();import psycopg
 with psycopg.connect(**s.connection_kwargs()) as c,c.cursor() as q:
  q.execute("""SELECT b.status,count(*),count(*) FILTER(WHERE decision_status='SELECTED'),count(*) FILTER(WHERE decision_status='NOT_SELECTED'),
                     count(*) FILTER(WHERE selection_tier='CORE'),count(*) FILTER(WHERE selection_tier='ACTIVE'),
                     count(*) FILTER(WHERE selection_tier='OBSERVE'),count(*) FILTER(WHERE selection_tier='NONE'),
                     count(*) FILTER(WHERE strong_recommendation),count(*) FILTER(WHERE approved_amount IS NOT NULL),
                     count(*)-count(DISTINCT s.strategy_id)
                FROM daily_strategy_selection_batch b JOIN daily_strategy_selection_snapshot s USING(selection_batch_id)
               WHERE b.selection_batch_id='DAILY_MA_SEL_20260824_V1' GROUP BY b.status""")
  snapshot=q.fetchone()
  q.execute("""SELECT count(*),count(*) FILTER(WHERE s.strategy_id IS NULL),
                     count(*) FILTER(WHERE actual_completed_trade_count>0 AND actual_compound_return_pct IS NULL),
                     count(*) FILTER(WHERE actual_completed_trade_count=0 AND actual_compound_return_pct IS NOT NULL)
                FROM vw_daily_strategy_selection_dashboard d
                LEFT JOIN daily_strategy_selection_snapshot s ON s.selection_batch_id='DAILY_MA_SEL_20260824_V1' AND s.strategy_id=d.strategy_id""")
  dashboard=q.fetchone()
  q.execute("SELECT count(*),count(*) FILTER(WHERE operation_status='LIVE'),coalesce(sum(allocated_amount),0) FROM daily_strategy_operation WHERE effective_to IS NULL")
  operations=q.fetchone()
  q.execute("SELECT count(*) FROM daily_strategy_selection_snapshot s LEFT JOIN daily_strategy_master m USING(strategy_id) WHERE m.strategy_role <> 'CANONICAL' OR m.is_enabled <> 'Y'")
  outside=q.fetchone()[0]
 print(json.dumps({'snapshot':snapshot,'dashboard':dashboard,'operations':operations,'canonical_outside_snapshot':outside},default=str))
 return 0
if __name__=='__main__':raise SystemExit(main())
