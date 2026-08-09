from datetime import datetime, timedelta
from decimal import Decimal
import unittest

from src.analysis.feature.sma_feature import MinuteBar
from src.analysis.feature.multi_ma_feature import MultiMaFeature
from src.service.research_complete_replay_service import CompleteReplay
from src.service.research_complete_replay_service import ResearchSignal


class CompleteReplayTest(unittest.TestCase):
    def test_complete_replay_is_raw_only_and_closes_open_position(self):
        start = datetime(2026, 8, 3, 9)
        values = [100, 99, 98, 97, 96, 97, 99, 102, 101, 98, 95, 93, 96, 100, 103]
        bars = [MinuteBar(start + timedelta(minutes=i), Decimal(v), Decimal(v), Decimal(v), Decimal(v)) for i, v in enumerate(values)]
        features, signals, cycles = CompleteReplay().run(bars)
        self.assertGreaterEqual(len(features), 1)
        self.assertTrue(all(signal.at == signal.feature.bar.bar_time for signal in signals))
        self.assertTrue(all(cycle.quantity >= 0 for cycle in cycles))
        self.assertTrue(all(cycle.exit_type in {"SIGNAL", "SESSION_CLOSE"} for cycle in cycles))

    def test_gap_does_not_mix_ma10_or_carry_position_to_next_session(self):
        start = datetime(2026, 8, 3, 9)
        first = [MinuteBar(start + timedelta(minutes=index), *(Decimal(100 + index),) * 4) for index in range(12)]
        # The 15:20~15:39 auction/NXT gap is intentionally not a continuous MA window.
        second_start = datetime(2026, 8, 3, 15, 40)
        second = [MinuteBar(second_start + timedelta(minutes=index), *(Decimal(200 + index),) * 4) for index in range(9)]
        features, _signals, _cycles = CompleteReplay().run(first + second)
        self.assertTrue(all(item.bar.bar_time < second_start for item in features))

    def test_exact_target_price_is_required_no_source_price_substitution(self):
        start = datetime(2026, 8, 3, 9)
        values = [100, 99, 98, 97, 96, 97, 99, 102, 101, 98, 95, 93, 96, 100, 103]
        bars = [MinuteBar(start + timedelta(minutes=i), *(Decimal(value),) * 4) for i, value in enumerate(values)]
        replay = CompleteReplay(); features = replay.features(bars); signals = replay.canonical_signals(features)
        cycles = replay.replay(features=features, signals=signals, target_prices={})
        self.assertEqual(cycles, [])

    def test_single_strategy_cycles_have_one_full_leg(self):
        start = datetime(2026, 8, 3, 9)
        values = [100,99,98,97,96,97,99,102,101,98,95,93,96,100,103,100,97,94,91]
        bars = [MinuteBar(start + timedelta(minutes=i), *(Decimal(value),) * 4) for i, value in enumerate(values)]
        _features, _signals, cycles = CompleteReplay().run(bars)
        singles = [cycle for cycle in cycles if cycle.strategy_code in {"SIGNAL_1","SIGNAL_2","SIGNAL_3"}]
        self.assertTrue(singles)
        self.assertTrue(all(len(cycle.legs) == 1 and cycle.legs[0].ratio == Decimal("1") for cycle in singles))

    def test_ma10_pending_confirms_on_first_matching_direction(self):
        start = datetime(2026, 8, 3, 9, 10)
        def feature(offset, ma10):
            bar = MinuteBar(start + timedelta(minutes=offset), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"))
            return MultiMaFeature(bar, Decimal("100"), Decimal("100"), Decimal("100"), Decimal(ma10), Decimal("1"))
        points = [feature(0, 100), feature(1, 100), feature(2, 101)]
        signal = ResearchSignal(points[1].bar.bar_time, "SIGNAL_1", "LONG", points[1])
        cycles = CompleteReplay().replay(features=points, signals=[signal], target_prices={item.bar.bar_time: item.value for item in points})
        s1 = next(item for item in cycles if item.strategy_code == "SIGNAL_1")
        self.assertEqual(s1.entry_signal_time, points[1].bar.bar_time)
        self.assertEqual(s1.entry_confirm_time, points[2].bar.bar_time)

    def test_pending_is_cancelled_by_opposite_signal(self):
        start = datetime(2026, 8, 3, 9, 10)
        def feature(offset, ma10):
            bar = MinuteBar(start + timedelta(minutes=offset), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"))
            return MultiMaFeature(bar, Decimal("100"), Decimal("100"), Decimal("100"), Decimal(ma10), Decimal("1"))
        points = [feature(0, 100), feature(1, 100), feature(2, 101)]
        long = ResearchSignal(points[1].bar.bar_time, "SIGNAL_1", "LONG", points[1])
        short = ResearchSignal(points[2].bar.bar_time, "SIGNAL_1", "SHORT", points[2])
        cycles = CompleteReplay().replay(features=points, signals=[long, short], target_prices={item.bar.bar_time: item.value for item in points})
        self.assertFalse(any(item.strategy_code == "SIGNAL_1" and item.direction == "LONG" for item in cycles))
