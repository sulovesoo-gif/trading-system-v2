"""SK하이닉스 통합 완료 1분봉 SMA 크로스 ntfy 알림 실행기.

주문 API와 주문 기능은 전혀 사용하지 않는다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=int, default=5)
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
        while True:
            try:
                now = kst_now()
                if now.weekday() < 5 and (now.hour, now.minute) >= (9, 1) and (now.hour, now.minute) <= (15, 31):
                    completed = []
                    for stock_code, market_code, venue in (
                        ("000660", "KOSPI", "KRX"), ("000660", "KOSPI", "NXT"), ("000660", "KOSPI", "INTEGRATED"),
                        ("0193T0", "ETF", "KRX"), ("0197X0", "ETF", "KRX"),
                    ):
                        rows = collector.collect(stock_code=stock_code, market_code=market_code, input_hour=now.strftime("%H%M%S"), trading_venue=venue)
                        rows = [row for row in rows if row["bar_time"] < now.replace(second=0, microsecond=0)]
                        RawIngestionService(raw_repository).store(RawTable.STOCK_MINUTE, rows)
                        if stock_code == "000660" and venue == "INTEGRATED":
                            completed = rows
                    if completed:
                        latest = max(row["bar_time"] for row in completed)
                        result = service.evaluate_completed_bar(stock_code="000660", completed_time=latest)
                        latest_bar = StockMinuteAnalysisRepository(pool).completed_bars(stock_code="000660", before_time=latest, limit=1)[-1]
                        service.update_open_performance(stock_code="000660", completed_bar=latest_bar)
                        if result:
                            print(f"신호 기록: {result} {latest}")
                        if latest.hour == 15 and latest.minute == 30:
                            service.close_market_performance(stock_code="000660", market_close_bar_time=latest)
            except Exception as error:
                print(f"1분봉 신호 처리 실패: {type(error).__name__}")
            time.sleep(args.interval_seconds)
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
