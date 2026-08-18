"""Read-only Forward universe audit; no candidate/order mutation."""
from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings

CORE_CODES={'S1_OR_PULLBACK_RESTART','S2_FAILED_OR_VWAP','S3_VOLUME_CLIMAX_REVERSAL'}
def main():
 import psycopg
 load_dotenv(ROOT/'.env');s=DatabaseSettings.from_environment()
 with psycopg.connect(**s.connection_kwargs()) as c,c.cursor() as q:
  q.execute('SELECT strategy_id,strategy_code,signal_stock_code,execution_stock_code FROM research_strategy_master ORDER BY strategy_id')
  rows=q.fetchall()
 executable=[r for r in rows if r[1] in CORE_CODES and r[2] and r[3]]
  reasons={str(r[0]):('CORE_UNSUPPORTED' if r[1] not in CORE_CODES else 'MAPPING_MISSING') for r in rows if r not in executable}
 print(json.dumps({'research_total_rows':len(rows),'executable_rows':len(executable),'non_executable_rows':len(rows)-len(executable),'non_executable_reasons':reasons},default=str))
if __name__=='__main__':raise SystemExit(main())
