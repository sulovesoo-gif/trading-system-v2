"""Run the FLOW V3 PAPER runtime; this process has no broker SEND adapter."""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.flow_v3.repository import FlowV3PostgresRepository
from src.flow_v3.runtime import FlowV3PaperRuntime
from src.repository.database import DatabaseSettings, create_connection_pool

KST = ZoneInfo("Asia/Seoul")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(ROOT / ".env")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    runtime = FlowV3PaperRuntime(FlowV3PostgresRepository(pool))
    stopping = False

    def stop(_signum, _frame) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            now = datetime.now(KST).replace(tzinfo=None)
            result = runtime.run_cycle(now=now)
            if any((result.completed_minutes,result.entry_events_created,
                    result.entry_lots_created,result.exit_signals_recorded,
                    result.normal_exits_closed,result.forced_eod_closed)):
                print(result,flush=True)
            if args.once:
                return 0
            time.sleep(max(args.poll_seconds,0.2))
        return 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
