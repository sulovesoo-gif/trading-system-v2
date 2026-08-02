from datetime import datetime, timedelta
from decimal import Decimal
import unittest

from src.analysis.event.multi_ma_event import detect_signals
from src.analysis.feature.multi_ma_feature import MultiMaFeature, build_multi_ma_features, price_value
from src.analysis.feature.sma_feature import MinuteBar


def bar(index, close):
    return MinuteBar(datetime(2026, 8, 3, 9, 0) + timedelta(minutes=index), Decimal(close), Decimal(close) + 2, Decimal(close) - 2, Decimal(close))


class MultiMaFeatureTest(unittest.TestCase):
    def test_allowed_price_fields_are_fixed_mapping(self):
        sample = bar(0, "100")
        self.assertEqual(price_value(sample, "HL2"), Decimal("100"))
        with self.assertRaises(ValueError):
            price_value(sample, "close_price; DROP TABLE")

    def test_configured_periods_create_three_mas(self):
        features = build_multi_ma_features([bar(i, str(100 + i)) for i in range(10)], short_period=3, mid_period=5, long_period=10, price_field="CLOSE")
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0].ma_short, Decimal("108"))
        self.assertEqual(features[0].ma_mid, Decimal("107"))
        self.assertEqual(features[0].ma_long, Decimal("104.5"))

    def test_signal_two_crosses_once(self):
        previous = MultiMaFeature(bar(0, "10"), Decimal("10"), Decimal("9"), Decimal("10"), Decimal("11"), Decimal("0"))
        current = MultiMaFeature(bar(1, "12"), Decimal("12"), Decimal("12"), Decimal("11"), Decimal("10"), Decimal("3"))
        signals = detect_signals(previous, current)
        self.assertTrue(any(item.signal_type == "SIGNAL_3" and item.direction == "LONG" for item in signals))
