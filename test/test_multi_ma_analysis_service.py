from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
import unittest

from src.analysis.feature.multi_ma_feature import MultiMaFeature
from src.analysis.feature.sma_feature import MinuteBar
from src.service.multi_ma_analysis_service import MultiMaAnalysisService, new_slot_states


def bar(index: int, close: int) -> MinuteBar:
    when = datetime(2026, 8, 3, 9, 0) + timedelta(minutes=index)
    value = Decimal(close)
    return MinuteBar(when, value, value, value, value)


class MultiMaAnalysisServiceTest(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            short_period=3, mid_period=5, long_period=10,
            price_field="CLOSE", include_in_progress=False,
        )

    def test_observation_uses_its_own_previous_feature(self):
        current_inputs = [bar(index, 100 + index) for index in range(11)]
        prior = MultiMaFeature(
            bar(0, 100), Decimal("100"), Decimal("108"), Decimal("110"), Decimal("111"), Decimal("0"),
        )
        result = MultiMaAnalysisService().analyze(
            completed_bars=current_inputs, in_progress_bar=None, ma_config=self.config,
            states=new_slot_states(), previous_feature=prior,
        )
        self.assertIsNotNone(result)
        self.assertTrue(any(signal.signal_type == "SIGNAL_1" and signal.direction == "LONG" for signal in result.signals))
        self.assertTrue(any(signal.signal_type == "SIGNAL_2" and signal.direction == "LONG" for signal in result.signals))

    def test_first_observation_is_baseline_without_signal(self):
        result = MultiMaAnalysisService().analyze(
            completed_bars=[bar(index, 100 + index) for index in range(11)],
            in_progress_bar=None, ma_config=self.config,
            states=new_slot_states(), previous_feature=None,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.signals, ())

