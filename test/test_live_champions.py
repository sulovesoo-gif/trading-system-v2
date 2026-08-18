import unittest
from decimal import Decimal

from src.live_registry import FROZEN_LIVE_CHAMPIONS


class FrozenLiveChampionTest(unittest.TestCase):
    def test_four_frozen_instances_have_stable_separate_identity(self):
        self.assertEqual([champion.strategy_id for champion in FROZEN_LIVE_CHAMPIONS], [294, 299, 802, 623])
        self.assertEqual(len({champion.live_name for champion in FROZEN_LIVE_CHAMPIONS}), 4)
        self.assertTrue(all(champion.initial_live_capital == Decimal("1000000") for champion in FROZEN_LIVE_CHAMPIONS))

    def test_only_s3_variants_share_an_entry_group(self):
        groups = {champion.strategy_id: champion.entry_group for champion in FROZEN_LIVE_CHAMPIONS}
        self.assertEqual(groups[294], groups[299])
        self.assertIsNone(groups[802])
        self.assertIsNone(groups[623])
