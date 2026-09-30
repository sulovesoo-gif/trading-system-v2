"""Run KIS FLOW RAW (two FLOW symbols and six execution symbols)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from threading import RLock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.flow_raw.collector import collector_from_environment
from src.flow_raw.repository import FlowRawRepository
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.collector.raw.kis_client import KISClient
from src.first_rise_breakout.condition_search import SavedConditionSearch
from src.first_rise_breakout.minute_source import SameDayMinutePeakSource
from src.first_rise_breakout.repository import FirstRiseBreakoutRepository
from src.first_rise_breakout.runtime import FirstRiseBreakoutRuntime
from src.first_rise_breakout.strategy import FirstRiseBreakoutStrategy
from src.minute_ma.integrated_realtime_repository import MinuteMaIntegratedRealtimeRepository
from src.repository.database import DatabaseSettings, create_connection_pool


async def run(pool) -> None:
    condition_request_lock = RLock()
    research = None
    try:
        client = KISClient()
        research = FirstRiseBreakoutRuntime(
            repository=FirstRiseBreakoutRepository(pool),
            strategy=FirstRiseBreakoutStrategy(),
            condition_search=SavedConditionSearch(
                client, user_id=os.getenv("KIS_USER", ""), request_lock=condition_request_lock,
            ),
            minute_source=SameDayMinutePeakSource(
                StockMinuteCollector(client), request_lock=condition_request_lock,
            ),
        )
    except Exception:
        # Optional research setup cannot prevent the established RAW streams.
        logging.exception("first-rise research setup disabled")


    collector = collector_from_environment(
        FlowRawRepository(pool),
        integrated_repository=MinuteMaIntegratedRealtimeRepository(pool),
    )
    tasks = [collector.run_forever()]
    if research is not None:
        tasks.append(research.run_forever())
    await asyncio.gather(*tasks)


def main() -> int:
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        asyncio.run(run(pool))
    except KeyboardInterrupt:
        return 0
    finally:
        pool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
