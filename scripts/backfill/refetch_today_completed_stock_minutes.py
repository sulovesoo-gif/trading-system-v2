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
from src.repository.raw_specs import RawTable, get_raw_spec
from src.service.raw_ingestion_service import RawIngestionService


def parse_kst(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stock-code", default="000660")
    parser.add_argument("--venue", default="INTEGRATED", choices=("KRX", "NXT", "INTEGRATED"))
    parser.add_argument("--start", required=True, help="KST, YYYY-MM-DDTHH:MM")
    parser.add_argument("--end", required=True, help="KST, YYYY-MM-DDTHH:MM")
    parser.add_argument("--apply-corrections", action="store_true", help="Replace only confirmed OHLCV mismatches in one transaction each.")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if "test" not in os.getenv("DB_NAME", "").lower():
        raise SystemExit("This bounded recovery is restricted to a test database.")
    start, end = parse_kst(args.start), parse_kst(args.end)
    if end < start:
        raise SystemExit("--end must not be earlier than --start")
    collector = StockMinuteCollector(KISClient())
    pool = create_connection_pool(DatabaseSettings.from_environment())
    stored = requested = pages = corrected = 0
    seen: set[datetime] = set()
    all_rows: dict[datetime, dict] = {}
    cursor = end
    try:
        repository = RawRepository(pool)
        ingestion = RawIngestionService(repository)
        while cursor >= start:
            rows = collector.collect(stock_code=args.stock_code, market_code="KOSPI", trading_venue=args.venue, input_hour=cursor.strftime("%H%M%S"), previous_data_include_yn="Y")
            pages += 1
            scoped = [row for row in rows if start <= row["bar_time"] <= end and row["bar_time"] not in seen]
            for row in scoped:
                seen.add(row["bar_time"])
                all_rows[row["bar_time"]] = row
            older = [row["bar_time"] for row in rows if row["bar_time"] < cursor]
            if not older:
                break
            next_cursor = min(older) - timedelta(microseconds=1)
            if next_cursor >= cursor:
                break
            cursor = next_cursor
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT bar_time,open_price,high_price,low_price,close_price,volume,accumulated_amount
            FROM raw_stock_minute WHERE stock_code=%s AND market_code='KOSPI' AND trading_venue=%s
            AND collect_cycle='1MIN' AND bar_time BETWEEN %s AND %s""", (args.stock_code, args.venue, start, end))
            existing = {row[0]: row[1:] for row in cur.fetchall()}
        # ``seen_rows`` retains the original KIS raw payload for comparison;
        # do not reconstruct any value from a snapshot.
        api_rows = all_rows
        fields = ("open_price", "high_price", "low_price", "close_price", "volume", "accumulated_amount")
        missing = [row for at, row in api_rows.items() if at not in existing]
        mismatches = [row for at, row in api_rows.items() if at in existing and tuple(row[name] for name in fields) != existing[at]]
        if missing:
            result = ingestion.store(RawTable.STOCK_MINUTE, missing)
            requested += result.requested_count; stored += result.inserted_count
        if mismatches and args.apply_corrections:
            spec = get_raw_spec(RawTable.STOCK_MINUTE)
            with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
                for row in mismatches:
                    cur.execute("""DELETE FROM raw_stock_minute WHERE bar_time=%s AND stock_code=%s AND market_code='KOSPI'
                    AND trading_venue=%s AND collect_cycle='1MIN' AND data_source=%s""", (row["bar_time"], args.stock_code, args.venue, row["data_source"]))
                    cur.execute(repository._insert_sql(spec, 1), repository._to_values(spec, row))
                    corrected += len(cur.fetchall())
        db_only = sorted(set(existing) - set(api_rows))
        print({"pages": pages, "api_rows_in_range": len(api_rows), "missing_inserted": stored, "identical": len(api_rows)-len(missing)-len(mismatches), "mismatches": len(mismatches), "corrected": corrected, "db_only": [at.isoformat() for at in db_only], "minimum": min(api_rows).isoformat() if api_rows else None, "maximum": max(api_rows).isoformat() if api_rows else None})
        return 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
