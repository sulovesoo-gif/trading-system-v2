"""Capture KIS REST orderbook responses as isolated 15:00-15:30 shadow evidence.

This script never writes ``raw_flow_orderbook_5s`` (or any database object).  It
keeps the original output1/output2 objects in a JSONL evidence file so REST and
H0STASP0 semantics can be compared before any merge contract is approved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, time as wall_time, timedelta
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.collector.raw.kis_client import KISClient

KST = ZoneInfo("Asia/Seoul")
PATH = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
TR_ID = "FHKST01010200"
SYMBOLS = ("005930", "000660")
WINDOW_START = wall_time(15, 0)
WINDOW_END = wall_time(15, 30)
CADENCE_SECONDS = 5
INTER_REQUEST_SECONDS = 0.1


def _now() -> datetime:
    return datetime.now(KST)


def _bucket_start(value: datetime) -> datetime:
    return value.replace(second=(value.second // CADENCE_SECONDS) * CADENCE_SECONDS, microsecond=0)


def _in_window(value: datetime) -> bool:
    return value.weekday() < 5 and WINDOW_START <= value.time() < WINDOW_END


def _capture(client: KISClient, stock_code: str, collected_at: datetime) -> dict:
    started = monotonic()
    payload = client.get(
        path=PATH,
        tr_id=TR_ID,
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
    )
    finished_at = _now()
    output1 = payload.get("output1")
    output2 = payload.get("output2")
    if not isinstance(output1, dict) or not isinstance(output2, dict):
        raise RuntimeError("KIS REST orderbook response lacks object output1/output2")
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "bucket_start": _bucket_start(collected_at).isoformat(),
        "requested_at": collected_at.isoformat(),
        "received_at": finished_at.isoformat(),
        "response_latency_ms": round((monotonic() - started) * 1000, 3),
        "stock_code": stock_code,
        "trading_venue": "KRX",
        "tr_id": TR_ID,
        "endpoint": PATH,
        "raw_source": "KIS_REST_SHADOW",
        "payload_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "output1": output1,
        "output2": output2,
    }


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()


def _sleep_until_next_bucket(now: datetime) -> None:
    next_second = ((now.second // CADENCE_SECONDS) + 1) * CADENCE_SECONDS
    if next_second >= 60:
        target = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    else:
        target = now.replace(second=next_second, microsecond=0)
    time.sleep(max((target - now).total_seconds(), 0.05))


def run(*, output: Path, once: bool, allow_outside_window: bool) -> int:
    client = KISClient()
    captured_buckets: set[str] = set()
    while True:
        observed_at = _now()
        if not allow_outside_window and observed_at.time() >= WINDOW_END:
            return 0
        if allow_outside_window or _in_window(observed_at):
            bucket = _bucket_start(observed_at).isoformat()
            if bucket not in captured_buckets:
                rows = []
                for index, stock_code in enumerate(SYMBOLS):
                    rows.append(_capture(client, stock_code, observed_at))
                    if index + 1 < len(SYMBOLS):
                        time.sleep(INTER_REQUEST_SECONDS)
                _append_jsonl(output, rows)
                captured_buckets.add(bucket)
                print(f"REST shadow bucket={bucket} rows={len(rows)} output={output}", flush=True)
                if once:
                    return 0
        elif once:
            raise SystemExit("outside official 15:00-15:30 KST shadow window")
        _sleep_until_next_bucket(_now())


def main() -> int:
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--allow-outside-window", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    return run(output=args.output, once=args.once, allow_outside_window=args.allow_outside_window)


if __name__ == "__main__":
    raise SystemExit(main())
