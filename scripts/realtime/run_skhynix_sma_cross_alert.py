"""SK하이닉스 통합 완료 1분봉 SMA 크로스 ntfy 알림 실행기.

주문 API와 주문 기능은 전혀 사용하지 않는다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.collector.raw.converters import kst_now
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.repository.raw_specs import RawTable
from src.repository.sma_cross_signal_repository import SmaCrossSignalRepository
from src.repository.stock_minute_analysis_repository import StockMinuteAnalysisRepository
from src.service.email_alert_service import EmailAlertService, EmailSettings
from src.service.ntfy_alert_service import NtfyAlertService, NtfySettings
from src.service.raw_ingestion_service import RawIngestionService
from src.service.sma_cross_signal_service import SmaCrossSignalService


OBSERVATION_START = clock_time(8, 1)
OBSERVATION_END = clock_time(20, 4)


def is_observation_time(now: datetime) -> bool:
    """NXT 장전부터 장후 마지막 완료 봉을 받을 때까지 KST 기준으로 수집한다."""
    return now.weekday() < 5 and OBSERVATION_START <= now.time() <= OBSERVATION_END


def new_completed_bar_times(rows: list[dict[str, object]], *, now: datetime, last_processed: datetime | None) -> list[datetime]:
    """현재 진행 중인 봉을 제외하고, 이번 프로세스에서 아직 평가하지 않은 완료 봉만 시간순으로 반환한다."""
    cutoff = now.replace(second=0, microsecond=0)
    return sorted({
        row["bar_time"] for row in rows
        if isinstance(row.get("bar_time"), datetime)
        and row["bar_time"] < cutoff
        and (last_processed is None or row["bar_time"] > last_processed)
    })


def completed_rows_for_storage(rows: list[dict[str, object]], *, now: datetime) -> list[dict[str, object]]:
    """현재 진행 중인 1분봉을 RAW에 최초 저장하지 않는다.

    raw_stock_minute는 기본키 충돌 시 값을 갱신하지 않으므로, 현재 분의 중간
    OHLCV가 먼저 저장되면 완료값으로 교체할 수 없다. 수집·저장은 실행기에서
    완료 봉으로 제한하고 Collector는 API 원문 변환만 수행한다.
    """
    cutoff = now.replace(second=0, microsecond=0)
    return [
        row for row in rows
        if isinstance(row.get("bar_time"), datetime) and row["bar_time"] < cutoff
    ]


def initial_analysis_watermark(now: datetime) -> datetime:
    """재시작 전 API가 반환하는 과거 완료 봉을 신규 신호로 재평가하지 않는다."""
    return now.replace(second=0, microsecond=0)


def next_minute_one_second(now: datetime) -> datetime:
    """Return the next KST ``HH:MM:01`` collection point."""
    candidate = now.replace(second=1, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(minutes=1)


def immediately_previous_completed_row(rows: list[dict[str, object]], *, now: datetime) -> dict[str, object] | None:
    """Select only the immediately preceding completed one-minute bar.

    The Collector still returns the API response unchanged. This runner chooses one
    completed row before RAW storage so an in-progress bar or older response rows
    are never written during the realtime cycle.
    """
    target_time = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    matches = [row for row in rows if row.get("bar_time") == target_time]
    return matches[0] if len(matches) == 1 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=int, default=5, help="호환용 인자이며 매분 01초 방식에서는 사용하지 않습니다.")
    parser.add_argument("--dry-run", action="store_true", help="이메일을 전송하지 않고 신호만 기록한다.")
    parser.add_argument("--allow-non-test-db", action="store_true", help="승인된 운영 DB 실행 시에만 명시한다.")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if "test" not in os.getenv("DB_NAME", "").lower() and not args.allow_non_test_db:
        raise RuntimeError("기본 실행은 테스트 DB만 허용합니다. 승인된 운영 DB는 --allow-non-test-db를 명시해야 합니다.")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        raw_repository = RawRepository(pool)
        signal_repository = SmaCrossSignalRepository(pool)
        if args.dry_run:
            alert_service = None
        elif os.getenv("ALERT_NTFY_ENABLED", "false").lower() == "true":
            alert_service = NtfyAlertService(NtfySettings.from_environment())
        elif os.getenv("ALERT_SMTP_ENABLED", "false").lower() == "true":
            alert_service = EmailAlertService(EmailSettings.from_environment())
        else:
            raise RuntimeError("기본 알림은 ntfy입니다. ALERT_NTFY_ENABLED=true를 설정하세요.")
        service = SmaCrossSignalService(
            minute_repository=StockMinuteAnalysisRepository(pool), signal_repository=signal_repository, email_service=alert_service
        )
        collector = StockMinuteCollector(KISClient())
        startup_time = kst_now()
        last_integrated_bar_time = startup_time.replace(second=0, microsecond=0) - timedelta(minutes=1)
        restored_arm = service.restore_armed_state(stock_code="000660", before_time=startup_time)
        if restored_arm is not None:
            print(f"ARMED 상태 복구: {restored_arm.armed_direction} {restored_arm.ma_cross_time}")
        while True:
            try:
                now = kst_now()
                if not is_observation_time(now):
                    time.sleep(1)
                    continue
                time.sleep(max(0.0, (next_minute_one_second(now) - now).total_seconds()))
                now = kst_now()
                if not is_observation_time(now):
                    continue
                rows = collector.collect(
                    stock_code="000660",
                    market_code="KOSPI",
                    trading_venue="INTEGRATED",
                    input_hour=now.strftime("%H%M%S"),
                )
                completed_row = immediately_previous_completed_row(rows, now=now)
                if completed_row is None:
                    print(f"직전 완료 봉 없음: {now:%Y-%m-%d %H:%M:%S}")
                    continue
                completed_time = completed_row["bar_time"]
                if not isinstance(completed_time, datetime) or completed_time <= last_integrated_bar_time:
                    continue
                RawIngestionService(raw_repository).store(RawTable.STOCK_MINUTE, completed_row)
                result = service.evaluate_completed_bar(stock_code="000660", completed_time=completed_time)
                completed_bars = StockMinuteAnalysisRepository(pool).completed_bars(
                    stock_code="000660", before_time=completed_time, limit=1
                )
                if completed_bars:
                    service.update_open_performance(stock_code="000660", completed_bar=completed_bars[-1])
                last_integrated_bar_time = completed_time
                if result:
                    print(f"신호 기록: {result} {completed_time}")
                if completed_time.hour == 15 and completed_time.minute == 30:
                    service.close_market_performance(stock_code="000660", market_close_bar_time=completed_time)
            except Exception as error:
                print(f"1분봉 신호 처리 실패: {type(error).__name__}")
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
