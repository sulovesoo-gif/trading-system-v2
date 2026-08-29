"""Run the two-symbol KIS FLOW RAW websocket collector."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.flow_raw.collector import collector_from_environment
from src.flow_raw.repository import FlowRawRepository
from src.repository.database import DatabaseSettings, create_connection_pool


def main() -> int:
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        asyncio.run(collector_from_environment(FlowRawRepository(pool)).run_forever())
    except KeyboardInterrupt:
        return 0
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
