"""Independent completed-minute RAW collector service entrypoint.

This is intentionally separate from the retired SMA/alert runner.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.collector.raw.converters import kst_now
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.collector.runtime.completed_minute_raw_collector import CompletedMinuteRawCollector
from src.collector.runtime.minute_raw_source_registry import MinuteRawSourceRegistry
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        runtime = CompletedMinuteRawCollector(
            collector=StockMinuteCollector(KISClient()), repository=RawRepository(pool),
            source_registry=MinuteRawSourceRegistry(pool),
        )
        while True:
            result = runtime.run_cycle(now=kst_now())
            print({code: getattr(value, "inserted_count", value) for code, value in result.items()}, flush=True)
            if args.once:
                return 0
            time.sleep(max(1, args.interval_seconds))
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
