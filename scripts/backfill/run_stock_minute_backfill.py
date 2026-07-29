"""테스트 DB용 KRX 주식·ETF 1분봉 백필 실행기.

주문 기능은 전혀 호출하지 않는다. DB_NAME에 test가 포함되고
DB_INTEGRATION_TEST=1인 경우에만 실행한다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.collector.raw.domestic_stock.holiday_calendar_collector import HolidayCalendarCollector
from src.collector.raw.domestic_stock.stock_historical_minute_collector import StockHistoricalMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.repository.backfill_repository import BackfillRepository
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.service.kis_trading_calendar import KisTradingCalendar
from src.service.raw_ingestion_service import RawIngestionService
from src.service.stock_minute_backfill_service import StockMinuteBackfillService, StockMinuteBackfillTarget


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_env() -> None:
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def validate_test_database() -> None:
    if os.getenv("DB_INTEGRATION_TEST") != "1":
        raise RuntimeError("DB_INTEGRATION_TEST=1이 필요합니다.")
    database_name = os.getenv("DB_NAME", "").lower()
    if "test" not in database_name:
        raise RuntimeError("DB_NAME에 test가 포함된 테스트 DB에서만 실행할 수 있습니다.")


def apply_test_schema(pool) -> None:
    ddl_dir = PROJECT_ROOT / "database" / "ddl"
    ddl_files = [
        "03_stock_master.sql", "04_backfill_job.sql", "05_backfill_segment.sql",
        "12_raw_stock_quote.sql", "13_raw_stock_execution.sql", "14_raw_stock_minute.sql",
        "15_raw_stock_daily.sql", "16_raw_futures_quote.sql", "17_raw_futures_minute.sql",
    ]
    with pool.connection() as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                for name in ddl_files:
                    cursor.execute((ddl_dir / name).read_text(encoding="utf-8"))
                cursor.execute((PROJECT_ROOT / "database" / "seed" / "01_stock_minute_backfill_targets.sql").read_text(encoding="utf-8"))


def report(pool, job_id: int, open_dates: list[date]) -> None:
    expected = [value.isoformat() for value in open_dates]
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stock_code, count(*), min(bar_time), max(bar_time), count(DISTINCT bar_time::date) "
                "FROM raw_stock_minute WHERE trading_venue = 'KRX' AND collect_cycle = '1MIN' "
                "GROUP BY stock_code ORDER BY stock_code"
            )
            for row in cursor.fetchall():
                print(f"종목={row[0]} 수집행={row[1]} 최초={row[2]} 최종={row[3]} 거래일수={row[4]}")
            cursor.execute(
                "SELECT instrument_code, count(*), sum(request_count), sum(returned_count), sum(inserted_count), "
                "sum(duplicate_count), count(*) FILTER (WHERE status = 'FAILED') "
                "FROM backfill_segment WHERE job_id = %s GROUP BY instrument_code ORDER BY instrument_code",
                (job_id,),
            )
            for row in cursor.fetchall():
                print(f"검증 종목={row[0]} 세그먼트={row[1]} 호출={row[2]} 조회={row[3]} 저장={row[4]} 중복={row[5]} 실패세그먼트={row[6]}")
            cursor.execute(
                "SELECT instrument_code, trade_date FROM backfill_segment "
                "WHERE job_id = %s AND status <> 'COMPLETED' ORDER BY instrument_code, trade_date",
                (job_id,),
            )
            incomplete = cursor.fetchall()
    print(f"거래일 수={len(expected)} 미완료 세그먼트={len(incomplete)}")
    for code, trade_date in incomplete:
        print(f"미완료: {code} {trade_date}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2026-05-27")
    parser.add_argument("--end-date", default="2026-07-28")
    parser.add_argument("--resume", type=int, metavar="JOB_ID")
    parser.add_argument("--max-segments", type=int, default=0, help="중단·재개 검증용 제한(0은 전체)")
    parser.add_argument("--request-interval", type=float, default=0.25)
    args = parser.parse_args()
    load_env()
    validate_test_database()
    start_date, end_date = parse_date(args.start_date), parse_date(args.end_date)
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        if not args.resume:
            apply_test_schema(pool)
        repository = BackfillRepository(pool)
        client = KISClient()
        calendar = KisTradingCalendar(HolidayCalendarCollector(client), call_interval_seconds=1.0)
        open_dates = calendar.open_dates(start_date, end_date)
        if args.resume:
            job_id = args.resume
            segments = repository.resumable_segments(job_id)
            if not segments:
                print(f"재개할 세그먼트가 없습니다: job_id={job_id}")
                report(pool, job_id, open_dates)
                return 0
        else:
            job_id = repository.create_job(job_type="STOCK_MINUTE_KRX", start_date=start_date, end_date=end_date)
            targets = [StockMinuteBackfillTarget(code, market, "KRX") for code, market in repository.stock_backfill_targets()]
            if len(targets) != 6:
                raise RuntimeError(f"1차 백필 대상은 6개여야 합니다. 현재={len(targets)}")
            segments = [
                repository.create_segment(job_id=job_id, instrument_code=target.stock_code, trading_venue="KRX", trade_date=trade_date)
                for trade_date in open_dates for target in targets
            ]
        repository.mark_job_running(job_id)
        targets_by_code = {code: StockMinuteBackfillTarget(code, market, "KRX") for code, market in repository.stock_backfill_targets()}
        service = StockMinuteBackfillService(
            collector=StockHistoricalMinuteCollector(client),
            ingestion_service=RawIngestionService(RawRepository(pool)),
            backfill_repository=repository,
            sleep=time.sleep,
        )
        processed = 0
        for segment in segments:
            service.run_segment(segment=segment, target=targets_by_code[segment.instrument_code], trade_date=segment.trade_date)
            processed += 1
            if args.max_segments and processed >= args.max_segments:
                print(f"의도적 중단 지점: job_id={job_id}, 처리 세그먼트={processed}")
                report(pool, job_id, open_dates)
                return 0
            time.sleep(args.request_interval)
        repository.mark_job_completed(job_id)
        print(f"완료: job_id={job_id}")
        report(pool, job_id, open_dates)
        return 0
    except Exception as error:
        if 'repository' in locals() and 'job_id' in locals():
            repository.mark_job_failed(job_id, type(error).__name__)
        print(f"실패: {type(error).__name__}: {error}")
        return 1
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
