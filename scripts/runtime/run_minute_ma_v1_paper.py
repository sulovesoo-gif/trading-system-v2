"""Minute MA V1.0 PAPER runner.  Broker submit code is intentionally absent."""
from __future__ import annotations
import argparse,json,os,sys
from datetime import date,datetime
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.minute_ma.repository import PostgresMinuteMaRepository
from src.minute_ma.v1_runtime import MinuteMaV1PaperRuntime
from src.repository.database import DatabaseSettings,create_connection_pool

def main()->int:
    parser=argparse.ArgumentParser();timing=parser.add_mutually_exclusive_group(required=True)
    timing.add_argument('--date',type=date.fromisoformat);timing.add_argument('--date-current',action='store_true')
    parser.add_argument('--write',action='store_true');args=parser.parse_args();load_dotenv(ROOT/'.env')
    write=args.write and os.getenv('MINUTE_MA_V1_PAPER_WRITE','N')=='Y'
    if args.write and not write:raise SystemExit('V1 PAPER write blocked')
    pool=create_connection_pool(DatabaseSettings.from_environment())
    try:
        day=datetime.now(ZoneInfo('Asia/Seoul')).date() if args.date_current else args.date
        repository=PostgresMinuteMaRepository(pool,write_enabled=write)
        result=MinuteMaV1PaperRuntime(repository).run_day(trading_date=day)
        telemetry_rows=repository.snapshot_v1_telemetry(snapshot_date=day) if write else 0
        print(json.dumps({'mode':'PAPER_WRITE' if write else 'NO_WRITE','result':result.__dict__,
                          'telemetry_snapshot_rows':telemetry_rows,
                          'broker_send_eligible':False,'order_post':0},sort_keys=True))
    finally:pool.close()
    return 0
if __name__=='__main__':raise SystemExit(main())
