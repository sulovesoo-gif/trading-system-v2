import unittest
from datetime import date,datetime
from decimal import Decimal as D
from src.broker.shared_cost_allocation import OwnedCheckpoint,allocate_shared_costs
from src.daily_ma_v03.broker_cost_allocation import BrokerCostSnapshot,BrokerCostTotals,BrokerCostStatus


class SharedCostTest(unittest.TestCase):
    def snapshot(self,final=True):
        return BrokerCostSnapshot(date(2026,9,8),'0193T0',BrokerCostTotals(D(101),D(53),D(17)),
          datetime(2026,9,9,10),final,BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK if final else BrokerCostStatus.PENDING_BROKER_COST)

    def fills(self):
        return [OwnedCheckpoint(family,1,f'{family}-{side}',1,side,1,D(amount))
          for family,amount in [('DAILY',100),('MINUTE',200),('FLOW',300)] for side in ('BUY','SELL')]

    def test_three_families_reconcile_once(self):
        status,rows=allocate_shared_costs(snapshot=self.snapshot(),checkpoints=self.fills())
        self.assertEqual(len(rows),6)
        self.assertEqual(sum(a.buy_fee for _,a in rows),101)
        self.assertEqual(sum(a.sell_fee for _,a in rows),53)
        self.assertEqual(sum(a.sell_tax for _,a in rows),17)
        self.assertEqual(len({key for key,_ in rows}),6)
        self.assertEqual((status,rows),allocate_shared_costs(snapshot=self.snapshot(),checkpoints=list(reversed(self.fills()))))

    def test_same_fill_cannot_belong_to_two_families(self):
        fills=self.fills()
        fills.append(OwnedCheckpoint('FLOW',2,'DAILY-BUY',1,'BUY',1,D(100)))
        with self.assertRaisesRegex(ValueError,'DUPLICATE_CHECKPOINT'):
            allocate_shared_costs(snapshot=self.snapshot(),checkpoints=fills)

    def test_pending_or_unattributed_never_allocated(self):
        self.assertEqual(allocate_shared_costs(snapshot=self.snapshot(False),checkpoints=self.fills())[1],())
        self.assertEqual(allocate_shared_costs(snapshot=self.snapshot(),checkpoints=self.fills(),unattributed_activity=True)[1],())

    def test_unknown_family_blocked(self):
        with self.assertRaisesRegex(ValueError,'UNKNOWN_COST_OWNER'):
            allocate_shared_costs(snapshot=self.snapshot(),checkpoints=[OwnedCheckpoint('OTHER',1,'x',1,'BUY',1,D(100))])


if __name__=='__main__': unittest.main()
