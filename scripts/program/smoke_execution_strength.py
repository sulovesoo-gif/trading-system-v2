"""Read-only KIS execution-strength smoke check; it does not write RAW."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from src.collector.raw.domestic_stock.stock_execution_collector import StockExecutionCollector
from src.collector.raw.kis_client import KISClient


def main() -> int:
    load_dotenv(ROOT / ".env")
    rows = StockExecutionCollector(KISClient()).collect(stock_code="000660", market_code="KOSPI", trading_venue="INTEGRATED", collect_cycle="5SEC")
    print(f"response_rows={len(rows)} tr_id=FHKST01010300 venue=UN")
    for row in rows[:5]:
        print(f"snapshot={row['snapshot_time']} strength={row['execution_strength']} execution_volume={row['execution_volume']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
