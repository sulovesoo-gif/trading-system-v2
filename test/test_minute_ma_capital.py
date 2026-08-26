from decimal import Decimal
import unittest
from src.minute_ma.capital import SettlementAmounts


class MinuteMaCapitalTest(unittest.TestCase):
    def test_actual_cost_net_pnl_contract(self):
        amounts=SettlementAmounts(Decimal('100000'),Decimal('103000'),Decimal('50'),
                                  Decimal('60'),Decimal('180'),Decimal('10'))
        self.assertEqual(amounts.gross_realized_pnl,Decimal('3000'))
        self.assertEqual(amounts.net_realized_pnl,Decimal('2700'))


if __name__=="__main__":unittest.main()
