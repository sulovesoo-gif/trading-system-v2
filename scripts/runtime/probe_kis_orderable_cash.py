"""Masked, read-only probe for KIS domestic-stock orderable cash.

It prints no account, token, secret, or cash amount and has no POST path.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from src.broker.cash_lookup import KISBrokerAvailableCashLookup
from src.collector.raw.kis_client import KISClient
from src.collector.raw.kis_order_account import KISOrderAccount


def main() -> int:
    load_dotenv(ROOT / ".env")
    lookup = KISBrokerAvailableCashLookup(client=KISClient(), account=KISOrderAccount.from_environment())
    result = lookup.orderable_cash(stock_code="005930", order_price=Decimal("1000"))
    print(json.dumps({"endpoint": "inquire-psbl-order", "method": "GET", "ord_psbl_cash": "PRESENT",
                      "cash_value_masked": True,
                      "includes_pending_order_reservations": result.includes_pending_order_reservations}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
