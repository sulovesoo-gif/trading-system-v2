"""Read-only KIS same-day completed-minute history for first-rise research."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from contextlib import nullcontext
import logging

from .models import MinuteBar
LOGGER = logging.getLogger(__name__)


class SameDayMinutePeakSource:
    def __init__(self, collector, *, request_lock=None, raw_repository=None) -> None:
        self.collector = collector
        self.request_lock = request_lock
        self.raw_repository = raw_repository
        self._cache = {}
        self._previous = {}
        self._previous_errors = {}
        self.request_count = 0
        self.audit_previous_close = True
        self.mode_counts = {"bootstrap": 0, "catch_up": 0, "incremental": 0}

    def discard(self, *, stock_code, business_date):
        self._cache.pop((business_date, stock_code), None)

    def previous_close(self, *, stock_code, business_date):
        key = (business_date, stock_code)
        if key in self._previous_errors:
            return None
        return self._previous.get(key)

    def _observe_previous(self, key, rows):
        if not self.audit_previous_close:return
        for row in rows:
            try:
                value = Decimal(str(row.get('previous_close_price')))
                if not value.is_finite() or value <= 0:
                    raise ValueError('nonpositive/nonfinite')
                if key in self._previous and self._previous[key] != value:
                    raise ValueError('inconsistent')
                self._previous[key] = value
            except (InvalidOperation, ValueError):
                self._previous_errors[key] = 'INVALID_OR_INCONSISTENT_KIS_PREVIOUS_CLOSE'
                LOGGER.error('FIRST_RISE_PREVIOUS_CLOSE_ERROR date=%s stock_code=%s source=FHKST03010200 entry_blocked=true',*key)

    def _collect(self, **kwargs):
        try:
            return self.collector.collect(**kwargs)
        except InvalidOperation:
            # Preserve valid OHLC from the SAME response, without another GET.
            # Invalid previous-close is not a V2 signal gate; invalid amount is
            # retained as audit evidence and blocks actual liquidity sizing.
            from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
            from types import SimpleNamespace
            if not isinstance(self.collector, StockMinuteCollector):
                raise
            payload = self.collector.client.last_payload
            if not isinstance(payload, dict) or not isinstance(payload.get('output1'), dict):
                raise
            original = payload['output1'].get('stck_prdy_clpr')
            sanitized = {**payload, 'output1':dict(payload['output1']),
                         'output2':[dict(row) for row in payload.get('output2',[])]}
            changed=False
            try:
                Decimal(str(original).replace(',','').strip())
            except InvalidOperation:
                sanitized['output1']['stck_prdy_clpr']=None
                changed=True
            invalid_amounts={}
            for row in sanitized['output2']:
                value=row.get('acml_tr_pbmn')
                if value is None or value=='':continue
                try:Decimal(str(value).replace(',','').strip())
                except InvalidOperation:
                    invalid_amounts[(row.get('stck_bsop_date'),row.get('stck_cntg_hour'))]=value
                    row['acml_tr_pbmn']=None
                    changed=True
            if changed:
                parser = StockMinuteCollector(SimpleNamespace(get=lambda **_: sanitized))
                rows = parser.collect(**kwargs)
                for row in rows:
                    raw=row['raw_payload']
                    key=(raw.get('stck_bsop_date'),raw.get('stck_cntg_hour'))
                    row['raw_payload'] = {'output2':raw, 'original_stck_prdy_clpr':original,
                                          'invalid_acml_tr_pbmn':invalid_amounts.get(key)}
                return rows
            raise

    def bars_from_open(self, *, stock_code: str, until: datetime) -> list[dict]:
        start = datetime.combine(until.date(), time(9, 0))
        completed_before = until.replace(second=0, microsecond=0)
        for old_key in list(self._cache):
            if old_key[0] != until.date():
                del self._cache[old_key]
        for mapping in (self._previous, self._previous_errors):
            for old_key in list(mapping):
                if old_key[0] != until.date():
                    del mapping[old_key]
        key = (until.date(), stock_code)
        if key not in self._cache and self.raw_repository is not None:
            restored = self.raw_repository.load(stock_code=stock_code, as_of=until)
            if restored:
                self._observe_previous(key, restored)
                self._cache[key] = {row['bar_time']:row for row in restored}
        cached = self._cache.get(key, {})
        cursor = until
        found: dict[datetime, dict] = dict(cached)
        pages = 0
        while cursor >= start:
            with self.request_lock or nullcontext():
                self.request_count += 1
                pages += 1
                rows = self._collect(
                    stock_code=stock_code,
                    market_code="KOSPI",
                    trading_venue="KRX",
                    input_hour=cursor.strftime("%H%M%S"),
                    previous_data_include_yn="Y",
                )
            completed = [row for row in rows if start <= row['bar_time'] < completed_before
                         and row['bar_time'].time() <= time(15,30)]
            self._observe_previous(key, completed)
            if self.raw_repository is not None:
                completed = self.raw_repository.preserve(stock_code=stock_code,as_of=until,
                    rows=completed,mode='bootstrap' if not cached else ('catch_up' if pages>1 else 'incremental'))
            for row in completed:
                at = row["bar_time"]
                if start <= at < completed_before:
                    found.setdefault(at, row)
            if any(row["bar_time"] in cached for row in rows):
                break
            older = [row["bar_time"] for row in rows if row["bar_time"] < cursor]
            if not older:
                break
            next_cursor = min(older) - timedelta(microseconds=1)
            if next_cursor >= cursor:
                break
            cursor = next_cursor
        self._cache[key] = found
        mode = "bootstrap" if not cached else ("catch_up" if pages > 1 else "incremental")
        self.mode_counts[mode] += 1
        return [found[at] for at in sorted(found) if start <= at < completed_before]

    def completed_bars_from_open(self, *, stock_code: str, as_of: datetime) -> list[MinuteBar]:
        """Return only bars whose full one-minute interval ended by ``as_of``."""
        completed_before = as_of.replace(second=0, microsecond=0)
        rows = self.bars_from_open(stock_code=stock_code, until=as_of)
        return [
            MinuteBar.from_mapping(row)
            for row in rows
            if row["bar_time"] < completed_before
        ]

    def peak(self, *, stock_code: str, until: datetime) -> tuple[Decimal, datetime, Decimal] | None:
        bars = self.bars_from_open(stock_code=stock_code, until=until)
        priced = [bar for bar in bars if bar.get("high_price") and bar.get("close_price")]
        if not priced:
            return None
        peak_bar = max(priced, key=lambda row: (Decimal(row["high_price"]), -row["bar_time"].timestamp()))
        return Decimal(peak_bar["high_price"]), peak_bar["bar_time"], Decimal(priced[-1]["close_price"])
