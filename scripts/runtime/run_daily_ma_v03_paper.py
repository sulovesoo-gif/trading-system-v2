"""Explicit Daily MA V0.3 PAPER runner.  Defaults to NO_WRITE and has no broker path."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.daily_ma_v03.raw_provider import DailyMaRawProvider
from src.daily_ma_v03.repository import PostgresPaperRuntimeRepository
from src.daily_ma_v03.runtime import DailyMaPaperRuntime
from src.repository.database import DatabaseSettings, create_connection_pool


def _parse_at(value: str | None) -> datetime:
    if value is None:
        raise SystemExit("--at YYYY-MM-DDTHH:MM is required; this runner never guesses a clock time")
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise SystemExit("--at must be an ISO KST local timestamp") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--at", required=True, help="KST local 15:18 completed source bar timestamp")
    parser.add_argument("--write", action="store_true", help="guarded PAPER DB write; never a broker order")
    args = parser.parse_args()
    at = _parse_at(args.at)
    load_dotenv(ROOT / ".env")
    write_enabled = args.write and os.getenv("DAILY_MA_V03_PAPER_WRITE", "N") == "Y"
    if args.write and not write_enabled:
        raise SystemExit("PAPER write blocked: DAILY_MA_V03_PAPER_WRITE=Y is required")
    settings = DatabaseSettings.from_environment()
    pool = create_connection_pool(settings)
    try:
        repository = PostgresPaperRuntimeRepository(pool, write_enabled=write_enabled)
        runtime = DailyMaPaperRuntime(repository=repository, raw_provider=DailyMaRawProvider(pool))
        result = runtime.evaluate_1518(at)
        print(json.dumps({
            "mode": "PAPER_WRITE" if write_enabled else "NO_WRITE",
            "at": at.isoformat(),
            "canonical_loaded": len(repository.canonical_strategies()),
            "entry_signals": len(result),
            "entry_created": sum(item.entry_created for item in result),
            "no_execution_bar": sum(item.no_execution_bar for item in result),
            "broker_send_eligible": False,
            "order_post": 0,
        }, sort_keys=True))
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
