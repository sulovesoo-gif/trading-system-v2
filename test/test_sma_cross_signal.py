from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from src.analysis.event.sma_cross_event import detect_cross_signal, threshold_break
from src.analysis.feature.sma_feature import MinuteBar, build_sma_features
from src.repository.sma_cross_signal_repository import SmaCrossSignal
from src.service.sma_cross_signal_service import SmaCrossSignalService


BASE = datetime(2026, 7, 30, 9, 0)


def bars(closes: list[str]) -> list[MinuteBar]:
    return [MinuteBar(BASE + timedelta(minutes=index), Decimal(value), Decimal(value), Decimal(value), Decimal(value)) for index, value in enumerate(closes)]


class FakeMinuteRepository:
    def __init__(self, source, closes):
        self.source = source
        self.closes = [Decimal(value) for value in closes]

    def completed_bars(self, **_):
        return self.source

    def closes_since(self, **_):
        return self.closes

    def nearest_completed_bar(self, **_):
        return None


class FakeSignalRepository:
    def __init__(self, confirmed=None, candidate=None):
        self.confirmed = confirmed
        self.candidate = candidate
        self.created = []
        self.confirmed_candidates = []
        self.rejected = []
        self.performance = []

    def latest_confirmed(self, _): return self.confirmed
    def active_candidate(self, _): return self.candidate
    def signal_exists_at(self, **_): return False
    def create(self, values):
        signal = SmaCrossSignal(len(self.created) + 10, values["signal_time"], values["stock_code"], values["direction"], values["status"], values["signal_price"])
        self.created.append((signal, values))
        if signal.status in ("INITIAL_CONFIRMED", "CONFIRMED"):
            self.confirmed = signal
        return signal
    def confirm_candidate(self, **kwargs):
        self.confirmed_candidates.append(kwargs)
        value = self.candidate
        self.confirmed = SmaCrossSignal(value.signal_id, value.signal_time, value.stock_code, value.direction, "CONFIRMED", value.signal_price)
        return self.confirmed
    def reject_candidate(self, **kwargs): self.rejected.append(kwargs)
    def create_notification(self, **_): return False
    def signal_details(self, _): return {}
    def save_related_bar(self, **_): pass
    def ensure_performance(self, *_): pass
    def update_performance(self, **kwargs): self.performance.append(kwargs)


class SmaFeatureAndEventTest(unittest.TestCase):
    def test_up_cross_requires_both_sma_and_close_crosses(self):
        source = bars(["10"] * 10 + ["20"])
        source[-1] = MinuteBar(source[-1].bar_time, Decimal("10"), Decimal("20"), Decimal("10"), Decimal("20"))
        features = build_sma_features(source)
        event = detect_cross_signal(features[-2], features[-1])
        self.assertEqual((event.direction, event.direction_alignment), ("LONG", "ALIGNED"))

    def test_down_cross_and_candle_alignment(self):
        source = bars(["20"] * 10 + ["10"])
        source[-1] = MinuteBar(source[-1].bar_time, Decimal("20"), Decimal("20"), Decimal("10"), Decimal("10"))
        event = detect_cross_signal(*build_sma_features(source)[-2:])
        self.assertEqual((event.direction, event.direction_alignment), ("SHORT", "ALIGNED"))

    def test_threshold_uses_close_and_both_directions(self):
        self.assertEqual(threshold_break(Decimal("10.1"), Decimal("10"))[0], "UP")
        self.assertEqual(threshold_break(Decimal("9.9"), Decimal("10"))[0], "DOWN")
        self.assertIsNone(threshold_break(Decimal("10.009"), Decimal("10"))[0])


class SmaCrossSignalServiceTest(unittest.TestCase):
    def test_first_cross_is_initial_confirmed(self):
        repo = FakeSignalRepository()
        service = SmaCrossSignalService(
            minute_repository=FakeMinuteRepository(bars(["10"] * 10 + ["20"]), ["10"]), signal_repository=repo
        )
        self.assertEqual(service.evaluate_completed_bar(stock_code="000660", completed_time=BASE + timedelta(minutes=10)), "INITIAL_CONFIRMED")
        self.assertEqual(repo.created[0][0].status, "INITIAL_CONFIRMED")

    def test_cross_is_candidate_before_absolute_one_percent_move(self):
        baseline = SmaCrossSignal(1, BASE, "000660", "LONG", "CONFIRMED", Decimal("10"))
        repo = FakeSignalRepository(confirmed=baseline)
        service = SmaCrossSignalService(
            minute_repository=FakeMinuteRepository(bars(["10"] * 10 + ["20"]), ["10", "10.005"]), signal_repository=repo
        )
        self.assertEqual(service.evaluate_completed_bar(stock_code="000660", completed_time=BASE + timedelta(minutes=10)), "CANDIDATE")
        self.assertFalse(repo.created[0][1]["volatility_threshold_met"])

    def test_candidate_confirms_without_new_cross_when_down_threshold_breaks(self):
        baseline = SmaCrossSignal(1, BASE, "000660", "LONG", "CONFIRMED", Decimal("10"))
        candidate = SmaCrossSignal(2, BASE + timedelta(minutes=1), "000660", "LONG", "CANDIDATE", Decimal("10.1"))
        repo = FakeSignalRepository(confirmed=baseline, candidate=candidate)
        service = SmaCrossSignalService(
            minute_repository=FakeMinuteRepository(bars(["10"] * 10 + ["9.9"]), ["10", "9.9"]), signal_repository=repo
        )
        self.assertEqual(service.evaluate_completed_bar(stock_code="000660", completed_time=BASE + timedelta(minutes=10)), "CONFIRMED")
        self.assertEqual(repo.confirmed_candidates[0]["threshold_break_direction"], "DOWN")
        self.assertEqual(repo.confirmed_candidates[0]["threshold_direction_alignment"], "OPPOSED")

    def test_opposite_cross_rejects_candidate(self):
        baseline = SmaCrossSignal(1, BASE, "000660", "LONG", "CONFIRMED", Decimal("10"))
        candidate = SmaCrossSignal(2, BASE + timedelta(minutes=1), "000660", "LONG", "CANDIDATE", Decimal("19"))
        repo = FakeSignalRepository(confirmed=baseline, candidate=candidate)
        service = SmaCrossSignalService(
            minute_repository=FakeMinuteRepository(bars(["20"] * 10 + ["10"]), ["20", "20.005"]), signal_repository=repo
        )
        service.evaluate_completed_bar(stock_code="000660", completed_time=BASE + timedelta(minutes=10))
        self.assertEqual(repo.rejected[0]["reason"], "OPPOSITE_CROSS_BEFORE_THRESHOLD")
