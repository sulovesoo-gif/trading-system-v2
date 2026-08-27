import unittest

from src.ma_crossover import GapTransition, classify_gap_transition
from src.service.daily_ma_dashboard_service import proximity_signal_status


class SharedMaCrossoverTest(unittest.TestCase):
    def test_up_and_down_cross_contract(self):
        self.assertIs(classify_gap_transition(previous_gap=-1, current_gap=1), GapTransition.UP_CROSS)
        self.assertIs(classify_gap_transition(previous_gap=1, current_gap=-1), GapTransition.DOWN_CROSS)

    def test_near_requires_directional_approach_without_cross(self):
        self.assertIs(classify_gap_transition(previous_gap=-0.30, current_gap=-0.10, near_threshold=0.15), GapTransition.UP_NEAR)
        self.assertIs(classify_gap_transition(previous_gap=0.30, current_gap=0.10, near_threshold=0.15), GapTransition.DOWN_NEAR)
        self.assertIs(classify_gap_transition(previous_gap=-0.10, current_gap=-0.20, near_threshold=0.15), GapTransition.NO_CROSS)
        self.assertIs(classify_gap_transition(previous_gap=0.10, current_gap=0.20, near_threshold=0.15), GapTransition.NO_CROSS)

    def test_post_cross_same_side_is_not_a_repeated_cross(self):
        self.assertIs(classify_gap_transition(previous_gap=0.05, current_gap=0.08, near_threshold=0.15), GapTransition.NO_CROSS)
        self.assertIs(classify_gap_transition(previous_gap=-0.05, current_gap=-0.08, near_threshold=0.15), GapTransition.NO_CROSS)


class DailyMaProximityTest(unittest.TestCase):
    def status(self, direction, pe, ce, px, cx):
        return proximity_signal_status(
            direction=direction,
            previous_entry_gap_pct=pe,
            current_entry_gap_pct=ce,
            previous_exit_gap_pct=px,
            current_exit_gap_pct=cx,
        )[0]

    def test_up_cross_maps_to_long_entry_and_short_exit_only(self):
        self.assertEqual(self.status('LONG', -0.7, 0.1, -1, -1), 'ENTRY_CROSS_OBSERVED')
        self.assertIsNone(self.status('LONG', -1, -1, -0.7, 0.1))
        self.assertIsNone(self.status('SHORT', -0.7, 0.1, -1, -1))
        self.assertEqual(self.status('SHORT', -1, -1, -0.7, 0.1), 'EXIT_CROSS_OBSERVED')

    def test_down_cross_maps_to_short_entry_and_long_exit_only(self):
        self.assertIsNone(self.status('LONG', 0.7, -0.1, 1, 1))
        self.assertEqual(self.status('LONG', 1, 1, 0.7, -0.1), 'EXIT_CROSS_OBSERVED')
        self.assertEqual(self.status('SHORT', 0.7, -0.1, 1, 1), 'ENTRY_CROSS_OBSERVED')
        self.assertIsNone(self.status('SHORT', 1, 1, 0.7, -0.1))

    def test_near_maps_only_to_matching_strategy_direction(self):
        self.assertEqual(self.status('LONG', -0.3, -0.1, -1, -1), 'ENTRY_NEAR')
        self.assertEqual(self.status('LONG', 1, 1, 0.3, 0.1), 'EXIT_NEAR')
        self.assertEqual(self.status('SHORT', 0.3, 0.1, 1, 1), 'ENTRY_NEAR')
        self.assertEqual(self.status('SHORT', 1, 1, -0.3, -0.1), 'EXIT_NEAR')


if __name__ == '__main__':
    unittest.main()
