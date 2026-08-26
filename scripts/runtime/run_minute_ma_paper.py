"""Four-axis minute-MA PAPER runner. Broker code is intentionally absent."""
from __future__ import annotations

import argparse,json,os,sys
from datetime import date,datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

from dotenv import load_dotenv
from src.minute_ma.contracts import Axis
from src.minute_ma.repository import PostgresMinuteMaRepository
from src.minute_ma.runtime import MinuteMaPaperRuntime
from src.repository.database import DatabaseSettings,create_connection_pool


def main() -> int:
    parser=argparse.ArgumentParser()
    timing=parser.add_mutually_exclusive_group(required=True)
    timing.add_argument("--date",type=date.fromisoformat)
    timing.add_argument("--date-current",action="store_true")
    parser.add_argument("--axis",choices=[a.value for a in Axis])
    parser.add_argument("--write",action="store_true")
    args=parser.parse_args()
    load_dotenv(ROOT/".env")
    write_enabled=args.write and os.getenv("MINUTE_MA_PAPER_WRITE","N")=="Y"
    if args.write and not write_enabled:
        raise SystemExit("PAPER write blocked: MINUTE_MA_PAPER_WRITE=Y is required")
    pool=create_connection_pool(DatabaseSettings.from_environment())
    try:
        repository=PostgresMinuteMaRepository(pool,write_enabled=write_enabled)
        runtime=MinuteMaPaperRuntime(repository)
        axes=(Axis(args.axis),) if args.axis else tuple(Axis)
        trading_date=datetime.now(ZoneInfo("Asia/Seoul")).date() if args.date_current else args.date
        results={axis.value:runtime.run_day(trading_date=trading_date,axis=axis).__dict__ for axis in axes}
        print(json.dumps({"mode":"PAPER_WRITE" if write_enabled else "NO_WRITE",
                          "trading_date":trading_date.isoformat(),"axes":results,
                          "broker_send_eligible":False,"order_post":0},sort_keys=True))
    finally:
        pool.close()
    return 0


if __name__=="__main__":
    raise SystemExit(main())
