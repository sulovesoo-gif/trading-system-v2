"""Refetch a bounded range of official same-day minute bars from KIS.

The script deliberately never reads or writes snapshot rows and never deletes
RAW data.  It walks the KIS time cursor backwards and lets the RAW repository
apply its normal ``ON CONFLICT DO NOTHING`` policy.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.collector.raw.converters import combine_kst_datetime
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.repository.raw_specs import RawTable
from src.service.raw_ingestion_service import RawIngestionService


def parse_kst(value: str) -> datetime:
    return combine_kst_datetime(value[:10].replace("-", ""), value[11:].replace(":", ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-code", default="000660")
    parser.add_argument("--venue", default="INTEGRATED", choices=("KRX", "NXT", "INTEGRATED"))
    parser.add_argument("--start", required=True, help="KST, YYYY-MM-DDTHH:MM")
    parser.add_argument("--end", required=True, help="KST, YYYY-MM-DDTHH:MM")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if "test" not in os.getenv("DB_NAME", "").lower():
        raise SystemExit("This bounded recovery is restricted to a test database.")
    start, end = parse_kst(args.start), parse_kst(args.end)
    if end < start:
        raise SystemExit("--end must not be earlier than --start")
    collector = StockMinuteCollector(KISClient())
    pool = create_connection_pool(DatabaseSettings.from_environment())
    stored = requested = pages = 0
    seen: set[datetime] = set()
    cursor = end
    try:
        ingestion = RawIngestionService(RawRepository(pool))
        while cursor >= start:
            rows = collector.collect(stock_code=args.stock_code, market_code="KOSPI", trading_venue=args.venue, input_hour=cursor.strftime("%H%M%S"), previous_data_include_yn="Y")
            pages += 1
            scoped = [row for row in rows if start <= row["bar_time"] <= end and row["bar_time"] not in seen]
            for row in scoped:
                seen.add(row["bar_time"])
            if scoped:
                result = ingestion.store(RawTable.STOCK_MINUTE, scoped)
                requested += result.requested_count
                stored += result.inserted_count
            older = [row["bar_time"] for row in rows if row["bar_time"] < cursor]
            if not older:
                break
            next_cursor = min(older) - timedelta(microseconds=1)
            if next_cursor >= cursor:
                break
            cursor = next_cursor
        print({"pages": pages, "api_rows_in_range": len(seen), "requested": requested, "inserted": stored, "duplicates": requested - stored, "minimum": min(seen).isoformat() if seen else None, "maximum": max(seen).isoformat() if seen else None})
        return 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
