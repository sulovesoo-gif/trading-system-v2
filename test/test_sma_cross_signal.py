from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from src.analysis.event.sma_cross_event import detect_cross_signal, threshold_break
from src.analysis.feature.integrated_session import filter_integrated_analysis_bars
from src.analysis.feature.sma_feature import MinuteBar, SmaFeature, build_sma_features
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
        self.confirmed = SmaCrossSignal(
            value.signal_id, kwargs["confirmed_time"], value.stock_code, value.direction, "CONFIRMED",
            kwargs["confirmed_price"],
        )
        return self.confirmed
    def reject_candidate(self, **kwargs): self.rejected.append(kwargs)
    def create_notification(self, **_): return False
    def signal_details(self, _): return {}
    def save_related_bar(self, **_): pass
    def ensure_performance(self, *_): pass
    def update_performance(self, **kwargs): self.performance.append(kwargs)


class SmaFeatureAndEventTest(unittest.TestCase):
    def test_0849_to_0900_without_sma_cross_does_not_create_long(self):
        previous_bar = MinuteBar(datetime(2026, 7, 30, 8, 49), Decimal("1358000"), Decimal("1358000"), Decimal("1358000"), Decimal("1358000"))
        current_bar = MinuteBar(datetime(2026, 7, 30, 9, 0), Decimal("1361000"), Decimal("1389500"), Decimal("1358000"), Decimal("1383000"))
        previous = SmaFeature(previous_bar, Decimal("1365000"), Decimal("1355600"))
        current = SmaFeature(current_bar, Decimal("1369600"), Decimal("1359500"))
        self.assertIsNone(detect_cross_signal(previous, current))

    def test_integrated_gap_bars_are_excluded_without_filling(self):
        source = [
            MinuteBar(datetime(2026, 7, 30, 8, 49), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
            MinuteBar(datetime(2026, 7, 30, 8, 50), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
            MinuteBar(datetime(2026, 7, 30, 8, 59), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
            MinuteBar(datetime(2026, 7, 30, 9, 0), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
        ]
        self.assertEqual(
            [bar.bar_time for bar in filter_integrated_analysis_bars(source)],
            [datetime(2026, 7, 30, 8, 49), datetime(2026, 7, 30, 9, 0)],
        )
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
    def test_notification_body_labels_signal_time_as_kst(self):
        signal = SmaCrossSignal(1, BASE, "000660", "LONG", "CONFIRMED", Decimal("10"))
        body = SmaCrossSignalService._body(signal, {
            "candidate_time": BASE, "candidate_price": Decimal("10"),
            "confirmed_time": BASE + timedelta(minutes=2), "confirmed_price": Decimal("10.1"),
            "confirmed_change": Decimal("0.02"), "sma5": Decimal("10"), "sma10": Decimal("10"), "previous_price": Decimal("9.9"),
            "maximum_up": Decimal("0.01"), "maximum_down": Decimal("-0.01"), "threshold_met": True,
            "alignment": "ALIGNED", "threshold_direction": "UP", "threshold_alignment": "ALIGNED",
        })
        self.assertIn("후보 발생 시각(KST): 2026-07-30 09:00:00", body)
        self.assertIn("실제 확정 시각(KST): 2026-07-30 09:02:00", body)
        self.assertIn("실제 확정 종가: 10.1", body)

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
        self.assertEqual(repo.confirmed_candidates[0]["confirmed_time"], BASE + timedelta(minutes=10))
        self.assertEqual(repo.confirmed_candidates[0]["confirmed_price"], Decimal("9.9"))
        self.assertEqual(repo.confirmed_candidates[0]["confirmed_change_from_previous"], Decimal("-0.01"))

    def test_opposite_cross_rejects_candidate(self):
        baseline = SmaCrossSignal(1, BASE, "000660", "LONG", "CONFIRMED", Decimal("10"))
        candidate = SmaCrossSignal(2, BASE + timedelta(minutes=1), "000660", "LONG", "CANDIDATE", Decimal("19"))
        repo = FakeSignalRepository(confirmed=baseline, candidate=candidate)
        service = SmaCrossSignalService(
            minute_repository=FakeMinuteRepository(bars(["20"] * 10 + ["10"]), ["20", "20.005"]), signal_repository=repo
        )
        service.evaluate_completed_bar(stock_code="000660", completed_time=BASE + timedelta(minutes=10))
        self.assertEqual(repo.rejected[0]["reason"], "OPPOSITE_CROSS_BEFORE_THRESHOLD")
