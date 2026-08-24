from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest

from src.daily_ma_v03.broker_cost_allocation import BrokerCostSnapshot, BrokerCostStatus, BrokerCostTotals
from src.daily_ma_v03.broker_cost_finalization import next_krx_trading_date, stable_recheck

class _Calendar:
    def open_dates(self, start, end): return [date(2026, 8, 31)]

class CostFinalizationTest(unittest.TestCase):
    def snapshot(self, when):
        return BrokerCostSnapshot(date(2026, 8, 28), '005930', BrokerCostTotals(Decimal('3'), Decimal('4'), Decimal('5')), when, False, BrokerCostStatus.PENDING_BROKER_COST)

    def test_next_krx_day_not_calendar_weekend(self):
        self.assertEqual(next_krx_trading_date(trade_date=date(2026, 8, 28), calendar=_Calendar()), date(2026, 8, 31))

    def test_requires_t_plus_one_two_identical_fill_set_rechecks(self):
        t1 = datetime(2026, 8, 31, 8, 0)
        first = stable_recheck(stored=None, observed=self.snapshot(t1), fill_set_fingerprint='a'*64,
                               unattributed_activity=False, next_trade_date=date(2026, 8, 31))
        self.assertEqual(first.snapshot.status, BrokerCostStatus.PENDING_BROKER_COST)
        final = stable_recheck(stored=first, observed=self.snapshot(t1 + timedelta(minutes=10)), fill_set_fingerprint='a'*64,
                               unattributed_activity=False, next_trade_date=date(2026, 8, 31))
        self.assertEqual(final.snapshot.status, BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK)

    def test_changed_fill_set_never_finalizes(self):
        t1 = datetime(2026, 8, 31, 8, 0)
        first = stable_recheck(stored=None, observed=self.snapshot(t1), fill_set_fingerprint='a'*64,
                               unattributed_activity=False, next_trade_date=date(2026, 8, 31))
        changed = stable_recheck(stored=first, observed=self.snapshot(t1 + timedelta(minutes=10)), fill_set_fingerprint='b'*64,
                                 unattributed_activity=False, next_trade_date=date(2026, 8, 31))
        self.assertEqual(changed.snapshot.status, BrokerCostStatus.PENDING_BROKER_COST)

if __name__ == '__main__': unittest.main()
