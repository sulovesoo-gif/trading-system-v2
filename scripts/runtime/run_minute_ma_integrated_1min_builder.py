"""Build Minute MA INTEGRATED completed 1MIN bars from H0UNCNT0 L0."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.minute_ma.integrated_realtime_repository import MinuteMaIntegratedRealtimeRepository
from src.repository.database import DatabaseSettings, create_connection_pool

KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    grace_ms = max(0, int(os.getenv("MINUTE_MA_INTEGRATED_FINALIZE_GRACE_MS", "2000")))
    poll_ms = max(50, int(os.getenv("MINUTE_MA_INTEGRATED_POLL_MS", "250")))
    pool = create_connection_pool(DatabaseSettings.from_environment())
    repository = MinuteMaIntegratedRealtimeRepository(pool)
    try:
        inserted = repository.run_startup_backlog(
            now=datetime.now(KST).replace(tzinfo=None), grace_ms=grace_ms)
        logging.info("Minute MA INTEGRATED startup replay inserted=%d", inserted)
        while True:
            inserted = repository.run_recent(
                now=datetime.now(KST).replace(tzinfo=None), grace_ms=grace_ms)
            if inserted:
                logging.info("Minute MA INTEGRATED realtime 1MIN inserted=%d", inserted)
            if args.once:
                return 0
            time.sleep(poll_ms / 1000)
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
