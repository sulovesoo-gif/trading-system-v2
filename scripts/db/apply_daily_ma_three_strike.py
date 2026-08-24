from __future__ import annotations
import os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
def main():
 load_dotenv(ROOT/'.env')
 if os.getenv('APPLY_DAILY_MA_THREE_STRIKE')!='YES':raise SystemExit('set APPLY_DAILY_MA_THREE_STRIKE=YES')
 s=DatabaseSettings.from_environment()
 if s.name!='trading_system_v2_test':raise SystemExit('three strike apply is restricted to trading_system_v2_test')
 import psycopg
 with psycopg.connect(**s.connection_kwargs()) as c,c.cursor() as q:q.execute((ROOT/'database/migrations/20260825_daily_ma_live_three_strike_additive.sql').read_text(encoding='utf-8'));c.commit()
 print('APPLIED Daily MA three-strike safety to TEST')
if __name__=='__main__':main()
