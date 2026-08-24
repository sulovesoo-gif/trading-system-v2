from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
def main():
 load_dotenv(ROOT/'.env');s=DatabaseSettings.from_environment();import psycopg
 with psycopg.connect(**s.connection_kwargs()) as c,c.cursor() as q:
  q.execute("SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_name='daily_strategy_operation' ORDER BY ordinal_position")
  columns=q.fetchall()
  q.execute("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='daily_strategy_operation'::regclass ORDER BY conname")
  print(json.dumps({'columns':columns,'constraints':q.fetchall()},default=str))
if __name__=='__main__':main()
