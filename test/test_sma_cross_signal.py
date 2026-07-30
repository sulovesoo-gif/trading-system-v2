from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from src.analysis.event.sma_cross_event import detect_cross_signal, detect_ma_cross
from src.analysis.feature.sma_feature import MinuteBar, SmaFeature
from src.repository.sma_cross_signal_repository import ArmedState, SmaCrossSignal
from src.service.sma_cross_signal_service import SmaCrossSignalService


BASE = datetime(2026, 7, 30, 9, 0)


def feature(offset: int, close: str, sma5: str, sma10: str, open_price: str | None = None) -> SmaFeature:
    price = Decimal(close)
    opening = Decimal(open_price) if open_price is not None else price
    bar = MinuteBar(BASE + timedelta(minutes=offset), opening, price, price, price)
    return SmaFeature(bar, Decimal(sma5), Decimal(sma10))


class FakeMinuteRepository:
    def __init__(self, range_bars: list[MinuteBar] | None = None) -> None:
        self.range_bars = range_bars or []

    def completed_bars(self, **_):
        return []

    def bars_since(self, **_):
        return self.range_bars

    def nearest_completed_bar(self, **_):
        return None


class FakeSignalRepository:
    def __init__(self, *, confirmed=None, candidate=None, armed=None) -> None:
        self.confirmed = confirmed
        self.candidate = candidate
        self.armed = armed
        self.created = []
        self.confirmed_candidates = []
        self.rejected = []
        self.cleared = []
        self.arm_updates = []

    def latest_confirmed(self, _): return self.confirmed
    def active_candidate(self, _): return self.candidate
    def armed_state(self, _): return self.armed
    def signal_exists_at(self, **_): return False
    def upsert_arm(self, **kwargs):
        self.arm_updates.append(kwargs)
        self.armed = ArmedState(kwargs['stock_code'], kwargs['armed_direction'], kwargs['ma_cross_time'], kwargs['ma_cross_price'], kwargs['ma_cross_sma5'], kwargs['ma_cross_sma10'], None)
        return self.armed
    def clear_arm(self, stock_code): self.cleared.append(stock_code); self.armed = None
    def set_arm_candidate(self, **kwargs): self.arm_candidate = kwargs
    def create(self, values):
        signal = SmaCrossSignal(len(self.created) + 10, values['signal_time'], values['stock_code'], values['direction'], values['status'], values['signal_price'])
        self.created.append((signal, values))
        if signal.status in ('INITIAL_CONFIRMED', 'CONFIRMED'):
            self.confirmed = signal
        return signal
    def confirm_candidate(self, **kwargs):
        self.confirmed_candidates.append(kwargs)
        self.confirmed = SmaCrossSignal(self.candidate.signal_id, kwargs['confirmed_time'], self.candidate.stock_code, self.candidate.direction, 'CONFIRMED', kwargs['confirmed_price'])
        return self.confirmed
    def reject_candidate(self, **kwargs): self.rejected.append(kwargs)
    def create_notification(self, **_): return False
    def signal_details(self, _): return {}
    def save_related_bar(self, **_): pass
    def ensure_performance(self, *_): pass
    def update_performance(self, **_): pass


class ArmedSmaCrossServiceTest(unittest.TestCase):
    def evaluate(self, service, previous, current):
        with patch('src.service.sma_cross_signal_service.build_sma_features', return_value=[previous, current]):
            return service.evaluate_completed_bar(stock_code='000660', completed_time=current.bar.bar_time)

    def test_ma_cross_arms_without_same_bar_signal(self):
        previous = feature(0, '10', '10', '10')
        current = feature(1, '9', '11', '10')
        repo = FakeSignalRepository()
        service = SmaCrossSignalService(minute_repository=FakeMinuteRepository(), signal_repository=repo)
        self.assertIsNone(self.evaluate(service, previous, current))
        self.assertEqual(repo.arm_updates[0]['armed_direction'], 'LONG')
        self.assertEqual(repo.created, [])

    def test_following_close_cross_creates_initial_confirmed_and_clears_arm(self):
        arm = ArmedState('000660', 'LONG', BASE, Decimal('9'), Decimal('11'), Decimal('10'), None)
        previous = feature(1, '9', '11', '10')
        current = feature(2, '11', '11', '10', '10')
        repo = FakeSignalRepository(armed=arm)
        service = SmaCrossSignalService(minute_repository=FakeMinuteRepository(), signal_repository=repo)
        self.assertEqual(self.evaluate(service, previous, current), 'INITIAL_CONFIRMED')
        self.assertEqual(repo.created[0][0].signal_time, BASE + timedelta(minutes=2))
        self.assertEqual(repo.created[0][1]['ma_cross_time'], BASE)
        self.assertEqual(repo.cleared, ['000660'])

    def test_range_under_one_percent_creates_candidate(self):
        baseline = SmaCrossSignal(1, BASE, '000660', 'LONG', 'CONFIRMED', Decimal('100'))
        arm = ArmedState('000660', 'SHORT', BASE + timedelta(minutes=1), Decimal('100'), Decimal('9'), Decimal('10'), None)
        previous = feature(2, '10', '9', '10')
        current = feature(3, '9.8', '9', '10')
        range_bars = [
            MinuteBar(BASE, Decimal('100'), Decimal('100'), Decimal('100'), Decimal('100')),
            MinuteBar(BASE + timedelta(minutes=3), Decimal('99.5'), Decimal('99.5'), Decimal('99.5'), Decimal('99.5')),
        ]
        repo = FakeSignalRepository(confirmed=baseline, armed=arm)
        service = SmaCrossSignalService(minute_repository=FakeMinuteRepository(range_bars), signal_repository=repo)
        self.assertEqual(self.evaluate(service, previous, current), 'CANDIDATE')
        self.assertFalse(repo.created[0][1]['volatility_threshold_met'])
        self.assertEqual(repo.created[0][1]['close_range_return'], Decimal('100') / Decimal('99.5') - Decimal('1'))

    def test_candidate_confirms_from_close_range_without_new_price_cross(self):
        baseline = SmaCrossSignal(1, BASE, '000660', 'LONG', 'CONFIRMED', Decimal('100'))
        candidate = SmaCrossSignal(2, BASE + timedelta(minutes=1), '000660', 'SHORT', 'CANDIDATE', Decimal('99.8'))
        arm = ArmedState('000660', 'SHORT', BASE + timedelta(minutes=1), Decimal('99.8'), Decimal('9'), Decimal('10'), 2)
        previous = feature(3, '9.8', '9', '10')
        current = feature(4, '9.7', '9', '10')
        range_bars = [
            MinuteBar(BASE, Decimal('100'), Decimal('100'), Decimal('100'), Decimal('100')),
            MinuteBar(BASE + timedelta(minutes=2), Decimal('98'), Decimal('98'), Decimal('98'), Decimal('98')),
            MinuteBar(BASE + timedelta(minutes=4), Decimal('99.7'), Decimal('99.7'), Decimal('99.7'), Decimal('99.7')),
        ]
        repo = FakeSignalRepository(confirmed=baseline, candidate=candidate, armed=arm)
        service = SmaCrossSignalService(minute_repository=FakeMinuteRepository(range_bars), signal_repository=repo)
        self.assertEqual(self.evaluate(service, previous, current), 'CONFIRMED')
        self.assertEqual(repo.confirmed_candidates[0]['range_metrics']['highest_close_since_previous'], Decimal('100'))
        self.assertEqual(repo.confirmed_candidates[0]['range_metrics']['lowest_close_since_previous'], Decimal('98'))
        self.assertEqual(repo.cleared, ['000660'])

    def test_opposite_ma_cross_replaces_arm_and_rejects_opposite_candidate(self):
        candidate = SmaCrossSignal(2, BASE, '000660', 'LONG', 'CANDIDATE', Decimal('100'))
        previous = feature(0, '10', '10', '10')
        current = feature(1, '11', '9', '10')
        repo = FakeSignalRepository(candidate=candidate)
        service = SmaCrossSignalService(minute_repository=FakeMinuteRepository(), signal_repository=repo)
        self.evaluate(service, previous, current)
        self.assertEqual(repo.rejected[0]['reason'], 'OPPOSITE_MA_CROSS')
        self.assertEqual(repo.arm_updates[0]['armed_direction'], 'SHORT')

    def test_restart_restores_latest_ma_cross_without_creating_signal(self):
        previous = feature(0, '10', '10', '10')
        current = feature(1, '9', '9', '10')
        repo = FakeSignalRepository()
        service = SmaCrossSignalService(minute_repository=FakeMinuteRepository(), signal_repository=repo)
        with patch('src.service.sma_cross_signal_service.build_sma_features', return_value=[previous, current]):
            restored = service.restore_armed_state(stock_code='000660', before_time=current.bar.bar_time)
        self.assertEqual(restored.armed_direction, 'SHORT')
        self.assertEqual(restored.ma_cross_time, current.bar.bar_time)
        self.assertEqual(repo.created, [])


class LegacyComparisonTest(unittest.TestCase):
    def test_legacy_same_bar_rule_remains_available(self):
        previous = feature(0, '10', '10', '10')
        current = feature(1, '11', '11', '10')
        self.assertEqual(detect_cross_signal(previous, current).direction, 'LONG')
        self.assertEqual(detect_ma_cross(previous, current).direction, 'LONG')
