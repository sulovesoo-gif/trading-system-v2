"""Run the isolated Minute MA H0UNCNT0 collector."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.minute_ma.integrated_realtime_collector import collector_from_environment
from src.minute_ma.integrated_realtime_repository import MinuteMaIntegratedRealtimeRepository
from src.repository.database import DatabaseSettings, create_connection_pool


def main() -> int:
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        asyncio.run(collector_from_environment(
            MinuteMaIntegratedRealtimeRepository(pool)).run_forever())
    except KeyboardInterrupt:
        return 0
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
