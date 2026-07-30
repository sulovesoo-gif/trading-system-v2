"""2026-07-31 지정 구간의 KIS 통합 1분봉 이중 조회 ntfy 관찰 실행기.

RAW 저장, DB 접근, SMA 분석, 신호 판단, 주문 기능을 전혀 수행하지 않는다.
각 대상 완료 봉을 분 전환 뒤 01초와 02초에 한 번씩만 조회하고, 결과 두 개를
한 개의 ntfy 메시지로 묶어 총 30개 메시지를 전송한 뒤 종료한다.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.collector.raw.converters import kst_now
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.service.ntfy_alert_service import NtfyAlertService, NtfySettings


RUN_DATE = date(2026, 7, 31)
STOCK_CODE = "000660"
MARKET_CODE = "KOSPI"
TRADING_VENUE = "INTEGRATED"


@dataclass(frozen=True)
class FetchResult:
    queried_at: datetime
    bar_time: datetime | None
    open_price: object | None
    high_price: object | None
    low_price: object | None
    close_price: object | None
    volume: object | None
    accumulated_amount: object | None
    error: str | None = None

    def comparable_values(self) -> tuple[object | None, ...]:
        return (
            self.bar_time,
            self.open_price,
            self.high_price,
            self.low_price,
            self.close_price,
            self.volume,
            self.accumulated_amount,
        )


def target_bar_times(run_date: date) -> list[datetime]:
    return [
        *(datetime.combine(run_date, clock_time(8, minute)) for minute in range(10)),
        *(datetime.combine(run_date, clock_time(9, minute)) for minute in range(20)),
    ]


def wait_until(target_time: datetime) -> None:
    while True:
        remaining = (target_time - kst_now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.25))


def fetch_target_bar(collector: StockMinuteCollector, *, target_bar_time: datetime) -> FetchResult:
    queried_at = kst_now()
    try:
        rows = collector.collect(
            stock_code=STOCK_CODE,
            market_code=MARKET_CODE,
            trading_venue=TRADING_VENUE,
            input_hour=queried_at.strftime("%H%M%S"),
            previous_data_include_yn="Y",
        )
        current_minute = queried_at.replace(second=0, microsecond=0)
        matching = [
            row for row in rows
            if row.get("bar_time") == target_bar_time and target_bar_time < current_minute
        ]
        if len(matching) != 1:
            return FetchResult(
                queried_at=queried_at,
                bar_time=None,
                open_price=None,
                high_price=None,
                low_price=None,
                close_price=None,
                volume=None,
                accumulated_amount=None,
                error=f"TARGET_BAR_NOT_FOUND(count={len(matching)})",
            )
        row = matching[0]
        return FetchResult(
            queried_at=queried_at,
            bar_time=target_bar_time,
            open_price=row.get("open_price"),
            high_price=row.get("high_price"),
            low_price=row.get("low_price"),
            close_price=row.get("close_price"),
            volume=row.get("volume"),
            accumulated_amount=row.get("accumulated_amount"),
        )
    except Exception as error:
        return FetchResult(
            queried_at=queried_at,
            bar_time=None,
            open_price=None,
            high_price=None,
            low_price=None,
            close_price=None,
            volume=None,
            accumulated_amount=None,
            error=type(error).__name__,
        )


def format_result(label: str, result: FetchResult) -> str:
    if result.error:
        return f"{label} 조회={result.queried_at:%H:%M:%S} 결과={result.error}"
    return (
        f"{label} 조회={result.queried_at:%H:%M:%S} "
        f"OHLCV=({result.open_price}, {result.high_price}, {result.low_price}, {result.close_price}, {result.volume}) "
        f"누적거래대금={result.accumulated_amount}"
    )


def message_body(*, target_bar_time: datetime, first: FetchResult, second: FetchResult) -> str:
    same = first.error is None and second.error is None and first.comparable_values() == second.comparable_values()
    comparison = "SAME" if same else "DIFFERENT"
    return "\n".join(
        (
            f"대상: {STOCK_CODE} / {TRADING_VENUE}",
            f"대상 봉: {target_bar_time:%Y-%m-%d %H:%M} KST",
            format_result("01초", first),
            format_result("02초", second),
            f"비교: {comparison}",
            "관찰 전용입니다. RAW·DB·SMA·주문 기능은 사용하지 않습니다.",
        )
    )


def run() -> int:
    now = kst_now()
    if now.date() != RUN_DATE:
        raise RuntimeError(f"이 실행기는 {RUN_DATE.isoformat()} KST에만 실행할 수 있습니다.")
    collector = StockMinuteCollector(KISClient())
    alert_service = NtfyAlertService(NtfySettings.from_environment())
    for target_bar_time in target_bar_times(RUN_DATE):
        first_at = target_bar_time + timedelta(minutes=1, seconds=1)
        second_at = target_bar_time + timedelta(minutes=1, seconds=2)
        wait_until(first_at)
        first = fetch_target_bar(collector, target_bar_time=target_bar_time)
        wait_until(second_at)
        second = fetch_target_bar(collector, target_bar_time=target_bar_time)
        alert_service.send(
            subject=f"KIS 통합 1분봉 비교 {target_bar_time:%H:%M}",
            body=message_body(target_bar_time=target_bar_time, first=first, second=second),
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    load_dotenv(ROOT / ".env")
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
