"""Manual RAW-only shadow runner.  It never writes RAW unless --write is explicit.

Not a systemd entry point and not a collector cutover mechanism.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.collector.raw.converters import kst_now
from src.collector.raw.domestic_stock.program_collector import ProgramCollector
from src.collector.raw.domestic_stock.stock_execution_collector import StockExecutionCollector
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.collector.raw.runtime import RawCollectorRuntime
from src.collector.raw.shadow import RawShadowComparator
from src.repository.common_code_repository import CommonCodeRepository
from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.raw_repository import RawRepository
from src.service.raw_ingestion_service import RawIngestionService


def build_runtime(pool) -> RawCollectorRuntime:
    client = KISClient()
    return RawCollectorRuntime(
        codes=CommonCodeRepository(pool), raw_ingestion=RawIngestionService(RawRepository(pool)),
        minute_collector=StockMinuteCollector(client), program_collector=ProgramCollector(client),
        execution_collector=StockExecutionCollector(client), logger=print,
    )


def shadow_once(runtime: RawCollectorRuntime, comparator: RawShadowComparator, *, now, write: bool) -> dict:
    tick = runtime.collect_tick(now=now, store_records=write)
    comparisons = [asdict(comparator.compare(item.table, item.record)) for item in tick.records] if not write else []
    return {
        "observed_at": now.isoformat(), "shadow": not write, "skipped": tick.skipped,
        "records": len(tick.records), "failures": [asdict(item) for item in tick.failures],
        "comparisons": comparisons,
        "matched": sum(item["matched"] for item in comparisons),
        "missing": sum(not item["expected_found"] for item in comparisons),
        "mismatched": sum(item["expected_found"] and not item["matched"] for item in comparisons),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--duration", type=int, default=0)
    parser.add_argument("--write", action="store_true", help="explicit RAW write mode; shadow is the default")
    parser.add_argument("--allow-non-test-db", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if "test" not in os.getenv("DB_NAME", "").lower() and not args.allow_non_test_db:
        raise RuntimeError("테스트 DB만 허용합니다.")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        runtime, comparator = build_runtime(pool), RawShadowComparator(pool)
        deadline = time.monotonic() + args.duration
        reports = []
        while True:
            report = shadow_once(runtime, comparator, now=kst_now(), write=args.write)
            reports.append(report)
            print(json.dumps(report, ensure_ascii=False, default=str))
            if args.once or time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        return 0 if all(not item["failures"] for item in reports) else 1
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
