#!/usr/bin/env python3
"""Run the isolated Minute-MA + REAL PAPER backfill/incremental/EOD jobs."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from dotenv import load_dotenv

from src.minute_ma.real_paper_eod import MinuteMaRealPaperEod
from src.minute_ma.real_paper import RealFilter
from src.minute_ma.real_paper_runtime import MinuteMaRealPaperRuntime
from src.repository.database import DatabaseSettings,create_connection_pool


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=("backfill","incremental","eod"))
    parser.add_argument("--from-date",type=date.fromisoformat,default=date(2026,8,31))
    parser.add_argument("--to-date",type=date.fromisoformat,default=date(2026,9,23))
    parser.add_argument("--date",type=date.fromisoformat,default=date.today())
    parser.add_argument("--dry-run",action="store_true")
    parser.add_argument("--base-only",action="store_true")
    args=parser.parse_args()
    load_dotenv(ROOT/".env")
    pool=create_connection_pool(DatabaseSettings.from_environment())
    try:
        if args.mode=="backfill":
            filters=(RealFilter.BASE,) if args.base_only else None
            result=MinuteMaRealPaperRuntime(pool).backfill(
                args.from_date,args.to_date,dry_run=args.dry_run,filter_codes=filters)
            print(f"strategies={result.strategy_count} variants={result.variant_count} "
                  f"common_entries={result.common_entry_count} paper_trades={result.paper_trade_count}")
        elif args.mode=="incremental":
            opened,closed=MinuteMaRealPaperRuntime(pool).process_day(args.date)
            print(f"date={args.date} opened={opened} closed={closed}")
        else:
            rows,candidates=MinuteMaRealPaperEod(pool).refresh(args.date)
            print(f"date={args.date} snapshots={rows} candidates={candidates}")
    finally:
        pool.close()
    return 0


if __name__=="__main__": raise SystemExit(main())
