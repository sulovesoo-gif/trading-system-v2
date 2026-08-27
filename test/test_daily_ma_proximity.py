import unittest

from src.service.daily_ma_dashboard_service import proximity_signal_status


class DailyMaProximityTest(unittest.TestCase):
    def test_near_status_requires_pre_crossover_side(self):
        # LONG: ENTRY approaches from fast<=slow; EXIT approaches from fast>=slow.
        self.assertEqual(proximity_signal_status(direction='LONG', entry_gap_pct=-0.10, exit_gap_pct=1.0), 'ENTRY_NEAR')
        self.assertEqual(proximity_signal_status(direction='LONG', entry_gap_pct=-1.0, exit_gap_pct=0.10), 'EXIT_NEAR')
        self.assertIsNone(proximity_signal_status(direction='LONG', entry_gap_pct=0.10, exit_gap_pct=-0.10))

        # SHORT uses the inverse crossover sides.
        self.assertEqual(proximity_signal_status(direction='SHORT', entry_gap_pct=0.10, exit_gap_pct=1.0), 'ENTRY_NEAR')
        self.assertEqual(proximity_signal_status(direction='SHORT', entry_gap_pct=-1.0, exit_gap_pct=-0.10), 'EXIT_NEAR')
        self.assertIsNone(proximity_signal_status(direction='SHORT', entry_gap_pct=-0.10, exit_gap_pct=0.10))

    def test_post_cross_condition_hold_does_not_repeat_near(self):
        held_post_cross = [
            proximity_signal_status(direction='LONG', entry_gap_pct=0.03, exit_gap_pct=-0.03)
            for _ in range(3)
        ]
        self.assertEqual(held_post_cross, [None, None, None])


if __name__ == '__main__':
    unittest.main()
