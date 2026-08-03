"""Read-only smoke check for the KIS program-trade-by-stock response."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from src.collector.raw.domestic_stock.program_collector import ProgramCollector
from src.collector.raw.kis_client import KISClient


def main() -> int:
    load_dotenv(ROOT / ".env")
    rows = ProgramCollector(KISClient()).collect(stock_code="000660", market_code="KOSPI", trading_venue="INTEGRATED")
    print(f"response_rows={len(rows)}")
    for row in rows[:5]:
        print("snapshot=%s sell=%s buy=%s net=%s api_change=%s" % (
            row["snapshot_time"], row["sell_amount"], row["buy_amount"], row["net_buy_amount"], row["net_buy_amount_change"]
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
