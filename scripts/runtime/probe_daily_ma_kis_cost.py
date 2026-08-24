"""Read-only Daily MA KIS product-day cost adapter probe; never prints amounts."""
from __future__ import annotations
import sys
from datetime import date
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.collector.raw.kis_client import KISClient
from src.collector.raw.kis_order_account import KISOrderAccount
from src.daily_ma_v03.kis_cost_history import DailyMaKISProductDayCostLookup

def main() -> int:
    load_dotenv(ROOT / '.env')
    result = DailyMaKISProductDayCostLookup(client=KISClient(), account=KISOrderAccount.from_environment()).lookup(
        trade_date=date.today(), execution_stock_code='005930')
    nonnegative = all(value >= 0 for value in (result.totals.buy_fee, result.totals.sell_fee, result.totals.sell_tax, result.totals.other_cost))
    print(f'daily_ma_cost_read_only=PASS final={result.final} nonnegative={nonnegative}')
    return 0

if __name__ == '__main__': raise SystemExit(main())
