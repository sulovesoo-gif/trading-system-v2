"""One per-cycle V1 INTEGRATED source/MA evaluation shared by LIVE consumers.

The policy path's historical name is not a venue selector. v1_source_bars is
the sole price source; the existing V1 engine owns session/continuity/crosses.
"""
from __future__ import annotations

from .engine import MinuteMaSignalEngine


class OfficialSignalCycle:
    def __init__(self, repository, *, engine=None):
        self.repository = repository
        self.engine = engine or MinuteMaSignalEngine()
        self._bars = {}
        self._points = {}
        self._events = {}

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def v1_source_bars(self, *, stock_code, trading_date):
        key = (stock_code, trading_date)
        if key not in self._bars:
            self._bars[key] = self.repository.v1_source_bars(
                stock_code=stock_code, trading_date=trading_date)
        return self._bars[key]

    def prepare(self, *, path, bars):
        key = (path.signal_code, path.axis)
        if key not in self._points:
            bars = tuple(bars)
            if any(b.source_name not in (
                    'REST_1MIN_PRE_CUTOVER', 'KIS_H0UNCNT0_INTEGRATED') for b in bars):
                raise ValueError('OFFICIAL_V1_INTEGRATED_SOURCE_REQUIRED')
            self._points[key] = self.engine.prepare(path=path, bars=bars)
        return self._points[key]

    def evaluate_prepared(self, *, path, points):
        # Legacy cursor filtering and REAL route filtering consume the same
        # full-day crossover calculation, never independently rebuilt signals.
        if path.path_key not in self._events:
            full = self._points[(path.signal_code, path.axis)]
            self._events[path.path_key] = self.engine.evaluate_prepared(path=path, points=full)
        requested = {point.bar_time for point in points}
        return tuple(e for e in self._events[path.path_key] if e.source_bar_time in requested)

    def day(self, *, path, trading_date):
        points = self.prepare(path=path, bars=self.v1_source_bars(
            stock_code=path.signal_code, trading_date=trading_date))
        today = tuple(p for p in points if p.bar_time.date() == trading_date)
        return today, self.evaluate_prepared(path=path, points=today)


def signal_evidence(event):
    return {
        'signal_contract': 'MINUTE_MA_V1_INTEGRATED',
        'source_bar_time': event.source_bar_time.isoformat(),
        'confirmed_at': event.confirmed_at.isoformat(),
        'signal_source': event.signal_source,
        'official_signal_event_key': event.signal_event_key,
        'official_minute_path_id': event.minute_path_id,
        'ma': event.ma_values,
        'previous_ma': event.previous_ma_values,
    }
