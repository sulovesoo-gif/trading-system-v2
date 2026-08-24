"""Common-code scheduled official daily RAW collector; it never runs analysis or orders."""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.collector.raw.converters import kst_now
from src.collector.raw.domestic_stock.holiday_calendar_collector import HolidayCalendarCollector
from src.collector.raw.domestic_stock.stock_daily_collector import StockDailyCollector
from src.collector.raw.kis_client import KISClient
from src.repository.common_code_repository import CommonCodeRepository
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.service.kis_trading_calendar import KisTradingCalendar
from src.service.raw_ingestion_service import RawIngestionService
from src.service.stock_daily_backfill_service import StockDailyBackfillService
from src.service.stock_daily_collection_service import StockDailyCollectionService


def collect_and_log(runner: StockDailyCollectionService, trading_date: date) -> int:
    """Run one idempotent official-daily pass and leave per-target evidence."""
    failed = 0
    for item in runner.collect_trade_date(trading_date=trading_date):
        print(
            f"stock_daily stock_code={item.stock_code} venue={item.trading_venue} "
            f"status={item.status} inserted={item.inserted_count} duplicates={item.duplicate_count} "
            f"error={item.error or '-'}",
            flush=True,
        )
        failed += item.status == "FAILED"
    return int(failed)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=float, default=0.2)
    parser.add_argument("--allow-non-test-db", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--trade-date", type=date.fromisoformat)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if "test" not in os.getenv("DB_NAME", "").lower() and not args.allow_non_test_db:
        raise RuntimeError("--allow-non-test-db is required outside the test database")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        client = KISClient()
        codes = CommonCodeRepository(pool)
        runner = StockDailyCollectionService(
            code_repository=codes,
            calendar=KisTradingCalendar(HolidayCalendarCollector(client)),
            backfill_service=StockDailyBackfillService(
                collector=StockDailyCollector(client),
                ingestion_service=RawIngestionService(RawRepository(pool)),
            ),
        )
        if args.once:
            if args.trade_date is None:
                raise RuntimeError("--once requires --trade-date YYYY-MM-DD")
            return 1 if collect_and_log(runner, args.trade_date) else 0
        last_run_date = None
        while True:
            now = kst_now()
            schedule = codes.api_schedule("STOCK_DAILY_CLOSE")
            if schedule.due(now) and last_run_date != now.date():
                collect_and_log(runner, now.date())
                last_run_date = now.date()
            time.sleep(args.interval_seconds)
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
