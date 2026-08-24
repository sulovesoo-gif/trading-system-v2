from datetime import date, datetime
from decimal import Decimal
import unittest

from src.daily_ma_v03.kis_cost_history import DailyMaKISProductDayCostLookup


class _Client:
    def __init__(self): self.call = None
    def get(self, **kwargs):
        self.call = kwargs
        return {"output2": {"buy_fee_smtl": "2", "sll_fee_smtl": "3", "sll_tltx_smtl": "4"}}

class _Account: cano="masked"; account_product_code="01"

class KISCostHistoryTest(unittest.TestCase):
    def test_uses_one_product_one_day_authoritative_totals_and_is_pending(self):
        client = _Client()
        lookup = DailyMaKISProductDayCostLookup(client=client, account=_Account(), clock=lambda: datetime(2026, 8, 25, 18))
        result = lookup.lookup(trade_date=date(2026, 8, 25), execution_stock_code="005930")
        self.assertEqual(client.call["tr_id"], "TTTC8715R")
        self.assertEqual(client.call["params"]["PDNO"], "005930")
        self.assertEqual(client.call["params"]["INQR_STRT_DT"], "20260825")
        self.assertFalse(result.final)
        self.assertEqual(result.totals.buy_fee, Decimal("2"))
        self.assertEqual(result.totals.sell_fee + result.totals.sell_tax, Decimal("7"))

if __name__ == "__main__": unittest.main()
