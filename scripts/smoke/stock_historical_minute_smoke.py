"""실거래 주문 없이 KRX 주식·ETF 과거 1분봉 API만 확인하는 수동 스모크 스크립트."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.collector.raw.domestic_stock.stock_historical_minute_collector import StockHistoricalMinuteCollector
from src.collector.raw.kis_client import KISClient


KST = ZoneInfo("Asia/Seoul")
REQUIRED = ("KIS_BASE_URL", "KIS_API_KEY", "KIS_API_SECRET", "KIS_TEST_STOCK_CODE")


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


def main() -> int:
    load_env()
    missing = [key for key in REQUIRED if not os.getenv(key)]
    if missing:
        print(f"스킵: 필요한 환경 변수가 없습니다: {', '.join(missing)}")
        return 0
    collection_date = os.getenv("KIS_BACKFILL_SMOKE_DATE")
    if not collection_date:
        collection_date = (datetime.now(KST).date() - timedelta(days=1)).strftime("%Y%m%d")
    collector = StockHistoricalMinuteCollector(KISClient())
    try:
        rows = collector.collect(
            stock_code=os.environ["KIS_TEST_STOCK_CODE"],
            market_code=os.getenv("KIS_TEST_STOCK_MARKET_CODE", "KOSPI"),
            trading_venue="KRX",
            input_date=collection_date,
            input_hour=os.getenv("KIS_BACKFILL_SMOKE_HOUR", "153000"),
        )
    except Exception as error:
        print(f"실패: FHKST03010230 | {type(error).__name__}: {error}")
        return 1
    headers = collector.client.last_response_headers
    print("성공: FHKST03010230")
    print(f"요청 종목={os.environ['KIS_TEST_STOCK_CODE']} 거래소=KRX 날짜={collection_date}")
    print(f"RAW 행 수={len(rows)} tr_cont={headers.get('tr_cont', '') or '없음'}")
    if rows:
        times = [row["bar_time"] for row in rows]
        print(f"bar_time 범위={min(times)}~{max(times)} 원문 보존={all(row['raw_payload'] for row in rows)}")
        print(f"반환 정렬={'최신순' if times == sorted(times, reverse=True) else '과거순' if times == sorted(times) else '혼합'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
