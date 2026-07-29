"""테스트 DB용 KOSPI200 선물 월물별 과거 1분봉 백필 실행기."""

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
from src.collector.raw.futures.futures_minute_collector import FuturesMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.repository.backfill_repository import BackfillRepository
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.futures_backfill_manifest_repository import FuturesBackfillManifestRepository
from src.repository.raw_repository import RawRepository
from src.service.futures_minute_backfill_service import FuturesMinuteBackfillService, FuturesMinuteBackfillTarget
from src.service.kis_trading_calendar import KisTradingCalendar
from src.service.raw_ingestion_service import RawIngestionService


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
    if os.getenv("DB_NAME", "") != "trading_system_v2_test":
        raise RuntimeError("운영 DB 보호: DB_NAME은 trading_system_v2_test여야 합니다.")


def apply_test_schema(pool) -> None:
    ddl_dir = PROJECT_ROOT / "database" / "ddl"
    with pool.connection() as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                for name in (
                    "04_backfill_job.sql",
                    "05_backfill_segment.sql",
                    "06_futures_backfill_manifest.sql",
                    "17_raw_futures_minute.sql",
                ):
                    cursor.execute((ddl_dir / name).read_text(encoding="utf-8"))


def target_for_code(manifest_repository: FuturesBackfillManifestRepository, futures_code: str) -> FuturesMinuteBackfillTarget:
    manifest = manifest_repository.by_futures_code(
        instrument_key="KOSPI200_FUTURES",
        futures_code=futures_code,
    )
    return FuturesMinuteBackfillTarget(
        futures_code=manifest.futures_code,
        market_division_code=manifest.market_division_code,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2026-05-27")
    parser.add_argument("--end-date", default="2026-07-28")
    parser.add_argument("--resume", type=int, metavar="JOB_ID")
    parser.add_argument("--max-segments", type=int, default=0)
    parser.add_argument("--request-interval", type=float, default=1.0)
    args = parser.parse_args()

    load_env()
    validate_test_database()
    start_date, end_date = parse_date(args.start_date), parse_date(args.end_date)
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        if not args.resume:
            apply_test_schema(pool)
        backfill_repository = BackfillRepository(pool)
        manifest_repository = FuturesBackfillManifestRepository(pool)
        client = KISClient()
        calendar = KisTradingCalendar(HolidayCalendarCollector(client), call_interval_seconds=1.0)
        open_dates = calendar.open_dates(start_date, end_date)

        if args.resume:
            job_id = args.resume
            segments = backfill_repository.resumable_segments(job_id)
            if not segments:
                print(f"재개할 세그먼트가 없습니다: job_id={job_id}")
                return 0
        else:
            manifests = manifest_repository.active_for_range(
                instrument_key="KOSPI200_FUTURES",
                start_date=start_date,
                end_date=end_date,
            )
            if not manifests:
                raise RuntimeError("백필 기간에 활성화된 KOSPI200 선물 Manifest가 없습니다.")
            job_id = backfill_repository.create_job(
                job_type="FUTURES_MINUTE_KRX",
                start_date=start_date,
                end_date=end_date,
            )
            segments = []
            for trade_date in open_dates:
                for manifest in manifest_repository.active_on(
                    instrument_key="KOSPI200_FUTURES",
                    trade_date=trade_date,
                ):
                    segments.append(
                        backfill_repository.create_segment(
                            job_id=job_id,
                            instrument_code=manifest.futures_code,
                            trading_venue="KRX",
                            trade_date=trade_date,
                        )
                    )

        backfill_repository.mark_job_running(job_id)
        service = FuturesMinuteBackfillService(
            collector=FuturesMinuteCollector(client),
            ingestion_service=RawIngestionService(RawRepository(pool)),
            backfill_repository=backfill_repository,
            sleep=time.sleep,
        )
        for processed, segment in enumerate(segments, start=1):
            target = target_for_code(manifest_repository, segment.instrument_code)
            service.run_segment(segment=segment, target=target, trade_date=segment.trade_date)
            if args.max_segments and processed >= args.max_segments:
                print(f"의도적 중단 지점: job_id={job_id}, 처리 세그먼트={processed}")
                return 0
            time.sleep(args.request_interval)
        backfill_repository.mark_job_completed(job_id)
        print(f"완료: job_id={job_id}")
        return 0
    except Exception as error:
        if "backfill_repository" in locals() and "job_id" in locals():
            backfill_repository.mark_job_failed(job_id, type(error).__name__)
        print(f"실패: {type(error).__name__}: {error}")
        return 1
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
