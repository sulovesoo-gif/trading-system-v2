"""테스트 DB에 완료된 국내주식·ETF 일봉 RAW를 백필한다.

주문·분석·지표 계산을 수행하지 않는다. DB_NAME이 정확히
``trading_system_v2_test``이고 ``DB_INTEGRATION_TEST=1``일 때만 실행한다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.collector.raw.converters import kst_now
from src.collector.raw.domestic_stock.holiday_calendar_collector import HolidayCalendarCollector
from src.collector.raw.domestic_stock.stock_daily_collector import StockDailyCollector
from src.collector.raw.kis_client import KISClient
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.service.kis_trading_calendar import KisTradingCalendar
from src.service.raw_ingestion_service import RawIngestionService
from src.service.stock_daily_backfill_service import StockDailyBackfillService, StockDailyBackfillTarget


TARGETS = (
    StockDailyBackfillTarget("000660", "KOSPI", "KRX", date(2026, 1, 1)),
    StockDailyBackfillTarget("000660", "KOSPI", "INTEGRATED", date(2026, 1, 1)),
    StockDailyBackfillTarget("005930", "KOSPI", "KRX", date(2026, 1, 1)),
    StockDailyBackfillTarget("005930", "KOSPI", "INTEGRATED", date(2026, 1, 1)),
    StockDailyBackfillTarget("0193T0", "KOSPI", "KRX", date(2026, 5, 27)),
    StockDailyBackfillTarget("0197X0", "KOSPI", "KRX", date(2026, 5, 27)),
)


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


def last_completed_trade_date(calendar: KisTradingCalendar, *, today: date) -> date:
    candidates = calendar.open_dates(today - timedelta(days=14), today - timedelta(days=1))
    if not candidates:
        raise RuntimeError("최근 완료 거래일을 KIS 휴장일 API에서 찾지 못했습니다.")
    return candidates[-1]


def report_recent_rows(pool) -> None:
    requested = (("000660", "KRX"), ("000660", "INTEGRATED"), ("0193T0", "KRX"), ("0197X0", "KRX"))
    sql = (
        "SELECT trade_date, stock_code, trading_venue, open_price, high_price, low_price, "
        "close_price, volume, amount FROM raw_stock_daily "
        "WHERE stock_code = %s AND trading_venue = %s "
        "ORDER BY trade_date DESC LIMIT 3"
    )
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            for stock_code, venue in requested:
                cursor.execute(sql, (stock_code, venue))
                for row in reversed(cursor.fetchall()):
                    print(
                        "상세 "
                        f"trade_date={row[0]} stock_code={row[1]} trading_venue={row[2]} "
                        f"open_price={row[3]} high_price={row[4]} low_price={row[5]} "
                        f"close_price={row[6]} volume={row[7]} accumulated_amount={row[8]}"
                    )


def verify(pool, *, end_date: date) -> None:
    with pool.connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stock_code, trading_venue, count(*), min(trade_date), max(trade_date), "
                "count(*) FILTER (WHERE open_price IS NULL OR high_price IS NULL OR low_price IS NULL "
                "OR close_price IS NULL OR volume IS NULL OR amount IS NULL), "
                "count(*) FILTER (WHERE raw_payload ? 'stck_bsop_date' AND raw_payload ? 'stck_clpr') "
                "FROM raw_stock_daily WHERE stock_code IN ('000660', '005930', '0193T0', '0197X0') "
                "GROUP BY stock_code, trading_venue ORDER BY stock_code, trading_venue"
            )
            for row in cursor.fetchall():
                print(
                    f"검증 stock_code={row[0]} trading_venue={row[1]} rows={row[2]} "
                    f"minimum_trade_date={row[3]} maximum_trade_date={row[4]} "
                    f"missing_ohlcv_or_amount={row[5]} raw_payload_required_fields={row[6]}"
                )
            cursor.execute(
                "SELECT count(*) FROM (SELECT trade_date, data_source, market_code, trading_venue, "
                "collect_cycle, stock_code, count(*) FROM raw_stock_daily "
                "GROUP BY trade_date, data_source, market_code, trading_venue, collect_cycle, stock_code "
                "HAVING count(*) > 1) duplicates"
            )
            print(f"검증 기본키중복={cursor.fetchone()[0]}")
            cursor.execute("SELECT count(*) FROM raw_stock_daily WHERE trade_date > %s", (end_date,))
            print(f"검증 완료거래일이후행={cursor.fetchone()[0]}")
    report_recent_rows(pool)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--end-date", help="완료 거래일 상한(YYYY-MM-DD). 미지정 시 KIS 휴장일 API로 직전 완료 거래일을 사용")
    parser.add_argument("--max-days-per-request", type=int, default=90)
    parser.add_argument("--request-interval", type=float, default=1.0, help="KIS 일봉 API 호출 간격(초)")
    args = parser.parse_args()
    load_env()
    validate_test_database()
    if args.max_days_per_request < 1 or args.request_interval < 0:
        raise RuntimeError("요청 단위와 호출 간격은 유효한 값이어야 합니다.")

    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        client = KISClient()
        calendar = KisTradingCalendar(HolidayCalendarCollector(client), call_interval_seconds=1.0)
        end_date = parse_date(args.end_date) if args.end_date else last_completed_trade_date(
            calendar, today=kst_now().date()
        )
        service = StockDailyBackfillService(
            collector=StockDailyCollector(client),
            ingestion_service=RawIngestionService(RawRepository(pool)),
            sleep=time.sleep,
        )
        print(f"일봉 백필 종료 거래일={end_date}")
        failures: list[tuple[StockDailyBackfillTarget, Exception]] = []
        for target in TARGETS:
            try:
                result = service.run_target(
                    target=target,
                    end_date=end_date,
                    max_days_per_request=args.max_days_per_request,
                    request_interval_seconds=args.request_interval,
                )
                print(
                    f"수집 stock_code={target.stock_code} trading_venue={target.trading_venue} "
                    f"요청={result.requested_count} 저장={result.inserted_count} 중복={result.duplicate_count} "
                    f"최소={result.minimum_trade_date} 최대={result.maximum_trade_date}"
                )
            except Exception as error:
                failures.append((target, error))
                print(
                    f"실패 stock_code={target.stock_code} trading_venue={target.trading_venue} "
                    f"error={type(error).__name__}: {error}"
                )
        verify(pool, end_date=end_date)
        return 1 if failures else 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
