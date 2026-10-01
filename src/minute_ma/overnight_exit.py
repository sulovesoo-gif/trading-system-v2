"""V1.8.1 optional overnight exits; no entry/MA/capital policy changes."""
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from hashlib import sha256
import logging

from src.repository.common_code_repository import CommonCodeRepository
from .engine import SignalEvent, SignalType

UP = 'OVERNIGHT_UP_0903'
DOWNFLAT = 'OVERNIGHT_DOWNFLAT_0910'
EXIT_REASONS = frozenset(('NORMAL_EXIT', UP, DOWNFLAT))
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class OvernightEvent(SignalEvent):
    overnight_evidence: dict | None = None


def reason_for(event):
    return event.signal_source if isinstance(event, OvernightEvent) else 'NORMAL_EXIT'


def evidence_for(event):
    from .official_signals import signal_evidence
    evidence = signal_evidence(event)
    if isinstance(event, OvernightEvent):
        evidence.update(event.overnight_evidence or {})
        evidence['exit_reason'] = reason_for(event)
    return evidence


@dataclass(frozen=True)
class OvernightDirection:
    previous_date: date | None
    previous_close: Decimal | None
    opening_price: Decimal | None
    opening_time: datetime

    @property
    def reason(self):
        if self.previous_close is None or self.opening_price is None:
            return None
        return UP if self.opening_price > self.previous_close else DOWNFLAT


class OvernightPolicy:
    """One cycle snapshot; existing uncached SYSTEM_SWITCH reader is reused."""
    def __init__(self, pool):
        codes = CommonCodeRepository(pool)
        self.enabled = {
            UP: codes.switch_enabled('MINUTE_MA_OVERNIGHT_UP_0903_FORCE_EXIT_YN'),
            DOWNFLAT: codes.switch_enabled('MINUTE_MA_OVERNIGHT_DOWNFLAT_0910_FORCE_EXIT_YN'),
        }
        self.pool = pool
        self._directions = {}

    def direction(self, stock_code, trading_date, official_bars):
        key = (stock_code, trading_date)
        if key not in self._directions:
            # Same KRX daily source used by RawRepository's previous-close
            # contract. Do not replace with INTEGRATED after-hours daily close.
            with self.pool.connection() as c, c.cursor() as q:
                q.execute("""SELECT trade_date,close_price FROM raw_stock_daily
                  WHERE stock_code=%s AND data_source='KIS' AND market_code='KOSPI'
                    AND trading_venue='KRX' AND collect_cycle='DAILY'
                    AND trade_date<%s ORDER BY trade_date DESC,collected_at DESC LIMIT 1""",
                          (stock_code, trading_date))
                row = q.fetchone()
            opening_time = datetime.combine(trading_date, time(9))
            opening = next((b for b in official_bars if b.bar_time == opening_time
                            and b.signal_eligible), None)
            result = OvernightDirection(
                row[0] if row else None,
                Decimal(str(row[1])) if row and row[1] is not None and row[1] > 0 else None,
                Decimal(str(opening.open_price)) if opening else None, opening_time)
            self._directions[key] = result
            if result.reason is None:
                LOG.warning('MINUTE_MA_OVERNIGHT MISSING stock=%s date=%s previous_date=%s previous_close=%s open_0900=%s',
                            stock_code, trading_date, result.previous_date,
                            result.previous_close, result.opening_price)
        return self._directions[key]

    def event(self, path, trading_date, official_bars, normal_events, now):
        if not any(self.enabled.values()):
            return None
        direction = self.direction(path.signal_code, trading_date, official_bars)
        reason = direction.reason
        if reason is None or not self.enabled[reason]:
            return None
        deadline = datetime.combine(trading_date, time(9, 3) if reason == UP else time(9, 10))
        if now < deadline:
            return None
        # A normal completed-MA signal before the deadline owns the exit even
        # if its execution price is not yet available. Never replace it.
        if any(e.signal_type is SignalType.EXIT and
               e.source_bar_time.date() == trading_date and
               e.source_bar_time < deadline for e in normal_events):
            return None
        key = sha256(f'MINUTE_MA_V181|{path.path_key}|{deadline.isoformat()}|{reason}'.encode()).hexdigest()
        return OvernightEvent(path.minute_path_id, path.path_key, SignalType.EXIT,
            deadline, deadline, key, True, {}, {}, reason, {
                'exit_policy': 'MINUTE_MA_V1.8.1',
                'direction_previous_date': direction.previous_date.isoformat(),
                'direction_previous_close': str(direction.previous_close),
                'direction_open_0900': str(direction.opening_price),
                'direction_close_source': 'raw_stock_daily:KIS:KRX:DAILY',
                'direction_open_source': 'v1_source_bars:INTEGRATED',
                'direction_open_bar_time': direction.opening_time.isoformat(),
                'switch_up': self.enabled[UP], 'switch_downflat': self.enabled[DOWNFLAT],
            })
