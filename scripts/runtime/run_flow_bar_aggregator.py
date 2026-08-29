"""Rebuild recent FLOW L1 bars from durable L0 RAW and exit."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.flow_raw.repository import FlowRawRepository
from src.repository.database import DatabaseSettings, create_connection_pool

KST = ZoneInfo("Asia/Seoul")


def main() -> int:
    load_dotenv(ROOT / ".env")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        now = datetime.now(KST).replace(tzinfo=None)
        FlowRawRepository(pool).refresh_l1(now=now)
        print(f"FLOW L1 rebuild completed at {now.isoformat()}", flush=True)
        return 0
    finally:
        pool.close()


if __name__ == "__main__":
    raise SystemExit(main())
