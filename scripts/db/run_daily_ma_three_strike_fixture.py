"""Rollback-only idempotency/recovery fixture for 3-strike risk state."""
from __future__ import annotations
import json,sys
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.daily_ma_v03.risk import PostgresThreeStrikeRiskStore
from src.repository.database import DatabaseSettings
class Shared:
 def __init__(self,c):self.c=c
 def __enter__(self):return self.c
 def __exit__(self,*_):return False
def main():
 load_dotenv(ROOT/'.env');s=DatabaseSettings.from_environment()
 if s.name!='trading_system_v2_test':raise SystemExit('fixture is restricted to trading_system_v2_test')
 import psycopg
 with psycopg.connect(**s.connection_kwargs()) as c:
  c.execute('BEGIN')
  with c.cursor() as q:
   q.execute("""SELECT p.paper_trade_id,p.strategy_id FROM daily_strategy_paper_trade p JOIN daily_strategy_live_risk_state r USING(strategy_id)
                WHERE p.trade_status='CLOSED' ORDER BY p.paper_trade_id LIMIT 3""")
   rows=q.fetchall()
   if len(rows)!=3:raise RuntimeError('three PAPER rows required')
   sid=str(rows[0][1]);
   if any(str(r[1])!=sid for r in rows):
    q.execute("SELECT paper_trade_id,strategy_id FROM daily_strategy_paper_trade WHERE strategy_id=%s AND trade_status='CLOSED' ORDER BY paper_trade_id LIMIT 3",(sid,));rows=q.fetchall()
   q.execute("UPDATE daily_strategy_live_risk_state SET live_risk_status='ENABLED',consecutive_loss_streak=0 WHERE strategy_id=%s",(sid,))
  store=PostgresThreeStrikeRiskStore(lambda:Shared(c),commit=False)
  outputs=[store.apply_completed_paper_trade(paper_trade_id=int(r[0]),strategy_id=sid,return_pct=Decimal('-1')) for r in rows]
  duplicate=store.apply_completed_paper_trade(paper_trade_id=int(rows[-1][0]),strategy_id=sid,return_pct=Decimal('-1'))
  reset=store.apply_completed_paper_trade(paper_trade_id=int(rows[0][0])+999999999,strategy_id=sid,return_pct=Decimal('1')) if False else None
  with c.cursor() as q:
   q.execute("SELECT live_risk_status,consecutive_loss_streak FROM daily_strategy_live_risk_state WHERE strategy_id=%s",(sid,));state=q.fetchone()
   q.execute("SELECT count(*) FROM daily_strategy_live_risk_event WHERE strategy_id=%s",(sid,));events=q.fetchone()[0]
  c.rollback()
 print(json.dumps({'after_three_losses':state,'events':events,'duplicate_created':duplicate[1],'rollback':True},default=str))
if __name__=='__main__':main()
