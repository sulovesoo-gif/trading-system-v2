from __future__ import annotations
import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
MIGRATION=ROOT/'database/migrations/20260824_daily_strategy_selection_audit_additive.sql'
def main():
 load_dotenv(ROOT/'.env')
 if os.getenv('APPLY_DAILY_MA_SELECTION_AUDIT_SCHEMA')!='YES': raise SystemExit('set APPLY_DAILY_MA_SELECTION_AUDIT_SCHEMA=YES')
 s=DatabaseSettings.from_environment()
 if s.name!='trading_system_v2_test': raise SystemExit('selection schema apply is restricted to trading_system_v2_test')
 import psycopg
 with psycopg.connect(**s.connection_kwargs()) as c,c.cursor() as q:q.execute(MIGRATION.read_text(encoding='utf-8'));c.commit()
 print('APPLIED Daily MA selection audit schema to TEST')
if __name__=='__main__':main()
