from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
def main():
 load_dotenv(ROOT/'.env');s=DatabaseSettings.from_environment();import psycopg
 with psycopg.connect(**s.connection_kwargs()) as c,c.cursor() as q:
  q.execute("SELECT count(*),count(*) FILTER(WHERE live_risk_status='ENABLED'),count(*) FILTER(WHERE live_risk_status='THREE_STRIKE_SUSPENDED') FROM daily_strategy_live_risk_state");state=q.fetchone()
  q.execute("SELECT count(*)-count(DISTINCT paper_trade_id) FROM daily_strategy_live_risk_event");dups=q.fetchone()[0]
 print(json.dumps({'risk_states':state,'duplicate_paper_trade_events':dups}))
if __name__=='__main__':main()
