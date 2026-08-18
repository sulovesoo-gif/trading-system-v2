"""Print completed INTEGRATED 1MIN dates shared by the two Research sources."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
def main() -> int:
 import psycopg
 load_dotenv(ROOT/'.env')
 with psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs()) as c,c.cursor() as q:
  q.execute("""SELECT bar_time::date FROM raw_stock_minute WHERE stock_code='000660' AND collect_cycle='1MIN' AND trading_venue='INTEGRATED'
               INTERSECT SELECT bar_time::date FROM raw_stock_minute WHERE stock_code='005930' AND collect_cycle='1MIN' AND trading_venue='INTEGRATED'
               ORDER BY 1""")
  for (day,) in q.fetchall(): print(day.isoformat())
 return 0
if __name__=='__main__':raise SystemExit(main())
