"""Read-only export of the historical Research master backtest procedure."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
def main():
 import psycopg
 load_dotenv(ROOT/'.env');s=DatabaseSettings.from_environment()
 with psycopg.connect(**s.connection_kwargs()) as c,c.cursor() as q:
  q.execute("""SELECT p.oid::regprocedure::text,pg_get_functiondef(p.oid)
               FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
               WHERE p.proname='run_strategy_master_backtest'""")
  rows=q.fetchall()
 for signature,definition in rows:
  print('--- '+signature+' ---');print(definition)
 return 0 if rows else 2
if __name__=='__main__':raise SystemExit(main())
