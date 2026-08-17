"""Manual RAW-only shadow runner.

Not a systemd entry point and not a collector cutover mechanism.
This entry point never writes production RAW.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
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


def build_runtime(pool, *, fault_stock_code: str | None = None) -> RawCollectorRuntime:
    client = KISClient()
    fault_state = {"used": False}

    def shadow_fault(table, stock_code, _venue) -> None:
        if fault_stock_code and not fault_state["used"] and stock_code == fault_stock_code:
            fault_state["used"] = True
            raise RuntimeError("SHADOW_FAULT_SIMULATION")

    return RawCollectorRuntime(
        codes=CommonCodeRepository(pool), raw_ingestion=RawIngestionService(RawRepository(pool)),
        minute_collector=StockMinuteCollector(client), program_collector=ProgramCollector(client),
        execution_collector=StockExecutionCollector(client), logger=print,
        failure_injector=shadow_fault if fault_stock_code else None,
    )


def _milliseconds(start: datetime, end: datetime | None) -> float | None:
    return None if end is None else round((end - start).total_seconds() * 1000, 3)


def shadow_once(runtime: RawCollectorRuntime, comparator: RawShadowComparator, *, now) -> dict:
    """Run exactly one legacy schedule slot, read-only, and preserve timing evidence."""
    tick = runtime.collect_tick(now=now, store_records=False)
    comparisons = []
    for item in tick.records:
        compared = comparator.compare(item.table, item.record)
        detail = asdict(compared)
        detail.update({
            "data_type": item.table.value,
            "stock_code": item.stock_code,
            "trading_venue": item.trading_venue,
            "scheduled_at": item.scheduled_at.isoformat(),
            "shadow_requested_at": item.requested_at.isoformat(),
            "shadow_response_received_at": item.response_received_at.isoformat(),
            "shadow_canonical_ready_at": item.canonical_ready_at.isoformat(),
            "shadow_collected_at": str(item.record.get("collected_at") or ""),
            "existing_collected_at": compared.existing_collected_at.isoformat() if compared.existing_collected_at else None,
            "scheduled_to_requested_ms": _milliseconds(item.scheduled_at, item.requested_at),
            "requested_to_response_ms": _milliseconds(item.requested_at, item.response_received_at),
            "response_to_canonical_ms": _milliseconds(item.response_received_at, item.canonical_ready_at),
            "target_time": str(item.record.get("bar_time") or item.record.get("snapshot_time") or ""),
            "reason_category": "EXACT_MATCH" if compared.matched else ("EXTRA_SHADOW_RECORD" if not compared.expected_found else "CANONICAL_NORMALIZATION_MISMATCH"),
        })
        comparisons.append(detail)
    return {
        "observed_at": now.isoformat(), "shadow": True, "raw_write_count": 0, "skipped": tick.skipped,
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
    parser.add_argument("--shadow-delay-seconds", type=float, default=2.0,
                        help="delay each shadow request after the legacy slot to avoid same-second duplication")
    parser.add_argument("--report-path", type=Path, help="write read-only JSON audit artifact")
    parser.add_argument("--csv-path", type=Path, help="write flattened comparison audit CSV")
    parser.add_argument("--fault-stock-code", help="one shadow-only injected failure; never affects the active runner")
    parser.add_argument("--allow-non-test-db", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    if "test" not in os.getenv("DB_NAME", "").lower() and not args.allow_non_test_db:
        raise RuntimeError("테스트 DB만 허용합니다.")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        runtime, comparator = build_runtime(pool, fault_stock_code=args.fault_stock_code), RawShadowComparator(pool)
        deadline = time.monotonic() + args.duration
        reports = []
        dispatched_seconds = set()
        inspected_seconds = set()
        while True:
            now = kst_now()
            second_key = now.replace(microsecond=0)
            scheduled = args.once
            if not args.once and second_key not in inspected_seconds:
                scheduled = runtime.scheduled(now)
                inspected_seconds.add(second_key)
            if args.once or (scheduled and second_key not in dispatched_seconds):
                # Preserve the scheduled slot for canonical timestamps/input_hour,
                # but request after the active writer's same-second dispatch.
                if not args.once and args.shadow_delay_seconds:
                    time.sleep(args.shadow_delay_seconds)
                report = shadow_once(runtime, comparator, now=now)
                reports.append(report)
                dispatched_seconds.add(second_key)
                print(json.dumps(report, ensure_ascii=False, default=str))
                if args.once:
                    break
            if args.duration <= 0 or time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        if args.report_path:
            args.report_path.parent.mkdir(parents=True, exist_ok=True)
            args.report_path.write_text(json.dumps({"reports": reports}, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        if args.csv_path:
            rows = [comparison for report in reports for comparison in report["comparisons"]]
            args.csv_path.parent.mkdir(parents=True, exist_ok=True)
            fields = sorted({field for row in rows for field in row})
            with args.csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
        return 0 if all(not item["failures"] for item in reports) else 1
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
