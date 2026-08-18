"""Read-only Forward operational status; no candidate/order mutation."""
from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.forward.book import ForwardBookStatus,forward_book_cap_from_environment
from src.repository.database import DatabaseSettings
def main():
 import psycopg
 load_dotenv(ROOT/'.env');s=DatabaseSettings.from_environment()
 with psycopg.connect(**s.connection_kwargs()) as c,c.cursor() as q:
  q.execute("SELECT count(*) FROM forward_candidate WHERE active_yn='Y'");active=q.fetchone()[0]
  q.execute("SELECT count(*) FROM forward_candidate");total=q.fetchone()[0]
  q.execute("SELECT COALESCE(sum(quantity*average_cost),0) FROM execution_logical_position WHERE ownership_type='FORWARD'");gross=q.fetchone()[0]
  q.execute("SELECT stock_code,unattributed_quantity,status FROM execution_reconciliation_audit ORDER BY audit_id DESC LIMIT 20");recon=q.fetchall()
  q.execute("SELECT max(bar_time) FROM raw_stock_minute WHERE collect_cycle='1MIN'");latest=q.fetchone()[0]
  q.execute("SELECT attr1 FROM common_code WHERE group_cd='SYSTEM_SWITCH' AND code='GLOBAL_TRADE_YN'");global_trade=q.fetchone()[0]
  q.execute("SELECT count(*) FROM live_smoke_approval");approval=q.fetchone()[0]
 status=ForwardBookStatus(forward_book_cap_from_environment(),gross)
 print(json.dumps({'active_candidate_count':active,'candidate_count':total,'quantity':1,'latest_completed_1min':latest,'forward_gross_exposure':str(status.gross_exposure),'forward_book_cap':str(status.cap) if status.cap is not None else 'UNSET','remaining_capacity':str(status.remaining_capacity) if status.remaining_capacity is not None else 'UNSET','actual_send_blocked':not status.actual_send_allowed,'broker_send_eligible':False,'global_trade_yn':global_trade,'actual_approval_count':approval,'reconciliation':recon},default=str))
 return 0
if __name__=='__main__':raise SystemExit(main())
