"""Polling of two saved HTS conditions without order or websocket behavior."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time
from time import perf_counter
from zoneinfo import ZoneInfo


LOGGER = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


class OrderbookConditionResearchRuntime:
    CONDITION_NAMES = (
        "TSV2_호가연구_매도잔량우위_V1",
        "TSV2_호가연구_매수잔량우위_V1",
    )

    def __init__(
        self, *, repository, condition_search, start_time: time = time(9, 0),
        end_time: time = time(15, 20), poll_interval_seconds: int = 10,
        now_provider=None,
    ) -> None:
        if start_time >= end_time:
            raise ValueError("orderbook research start_time must precede end_time")
        if poll_interval_seconds <= 0:
            raise ValueError("orderbook research poll interval must be positive")
        self.repository = repository
        self.condition_search = condition_search
        self.start_time = start_time
        self.end_time = end_time
        self.poll_interval_seconds = poll_interval_seconds
        self.now = now_provider or (lambda: datetime.now(KST).replace(tzinfo=None))
        self._seq_date = None
        self._seq_by_name: dict[str, str] = {}

    @staticmethod
    def parse_time(value: str) -> time:
        try:
            return time.fromisoformat(value.strip())
        except ValueError as error:
            raise ValueError(f"invalid orderbook research time: {value}") from error

    def _resolve_seq(self, condition_name: str, at: datetime) -> str:
        if self._seq_date != at.date():
            self._seq_date = at.date()
            self._seq_by_name = {}
        if condition_name not in self._seq_by_name:
            self._seq_by_name[condition_name] = self.condition_search.resolve_seq(condition_name)
        return self._seq_by_name[condition_name]

    def poll_once(self, *, at: datetime) -> dict[str, int]:
        if not (self.start_time <= at.time() <= self.end_time):
            return {}
        counts: dict[str, int] = {}
        for condition_name in self.CONDITION_NAMES:
            started = perf_counter()
            seq = self._seq_by_name.get(condition_name, "UNRESOLVED")
            try:
                seq = self._resolve_seq(condition_name, at)
                candidates = self.condition_search.candidates(seq)
                self.repository.record_hits(
                    business_date=at.date(), poll_time=at, condition_name=condition_name,
                    condition_seq=seq, candidates=candidates,
                )
                counts[condition_name] = len(candidates)
                LOGGER.info(
                    "ORDERBOOK_CONDITION_POLL time=%s condition=%s seq=%s http_status=%s "
                    "kis_code=%s result_count=%d empty_result=%s elapsed_ms=%d",
                    at.isoformat(), condition_name, seq,
                    getattr(self.condition_search, "last_http_status", None) or "UNKNOWN",
                    getattr(self.condition_search, "last_kis_code", None) or "UNKNOWN",
                    len(candidates), str(not candidates).lower(),
                    round((perf_counter() - started) * 1000),
                )
            except Exception:
                LOGGER.exception(
                    "ORDERBOOK_CONDITION_POLL_FAILED time=%s condition=%s seq=%s elapsed_ms=%d",
                    at.isoformat(), condition_name, seq,
                    round((perf_counter() - started) * 1000),
                )
        return counts

    async def run_forever(self) -> None:
        while True:
            await asyncio.to_thread(self.poll_once, at=self.now())
            await asyncio.sleep(self.poll_interval_seconds)
