"""Strategy-free realtime RAW collection runtime.

This module deliberately owns only the existing realtime collection contract.
It does not import analysis, research, alerts, live trading, or order modules.
The production SMA runner continues to be the active collector until a later,
separately approved cutover.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as clock_time, timedelta
from typing import Callable, Mapping

from src.collector.raw.domestic_stock.program_collector import ProgramCollector
from src.collector.raw.domestic_stock.stock_execution_collector import StockExecutionCollector
from src.collector.raw.domestic_stock.stock_minute_collector import StockMinuteCollector
from src.repository.raw_specs import RawTable
from src.service.stock_minute_snapshot_service import SCHEDULED_SNAPSHOT_SECONDS, StockMinuteSnapshotService


def collection_window_active(now: datetime) -> bool:
    """Exact weekday/window contract used by the existing active runner."""
    return now.weekday() < 5 and clock_time(8, 1) <= now.time() <= clock_time(20, 4)


def completed_minute_row(rows: list[dict[str, object]], now: datetime) -> dict[str, object] | None:
    target = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
    matches = [row for row in rows if row.get("bar_time") == target]
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class RawCollectionRecord:
    table: RawTable
    stock_code: str
    trading_venue: str
    record: Mapping[str, object]
    scheduled_at: datetime
    requested_at: datetime
    response_received_at: datetime
    canonical_ready_at: datetime


@dataclass(frozen=True)
class RawCollectionFailure:
    table: RawTable
    stock_code: str
    trading_venue: str
    error_type: str
    scheduled_at: datetime
    requested_at: datetime | None = None


@dataclass
class RawCollectionTick:
    observed_at: datetime
    records: list[RawCollectionRecord] = field(default_factory=list)
    failures: list[RawCollectionFailure] = field(default_factory=list)
    skipped: bool = False


class RawCollectorRuntime:
    """Collect canonical RAW records using the legacy schedule, without strategy calls.

    ``store_records`` is intentionally opt-in.  Shadow callers keep it false
    and compare normalized records with the pre-existing canonical RAW rows.
    """

    execution_stock_code = "000660"

    def __init__(
        self,
        *,
        codes,
        raw_ingestion,
        minute_collector: StockMinuteCollector,
        program_collector: ProgramCollector,
        execution_collector: StockExecutionCollector,
        logger: Callable[[str], None] | None = None,
        failure_injector: Callable[[RawTable, str, str], None] | None = None,
    ) -> None:
        self.codes = codes
        self.raw_ingestion = raw_ingestion
        self.minute_collector = minute_collector
        self.program_collector = program_collector
        self.execution_collector = execution_collector
        self.log = logger or (lambda _message: None)
        self.failure_injector = failure_injector

    def scheduled(self, now: datetime) -> bool:
        """Return whether the legacy runner would dispatch this exact second."""
        program = self.codes.api_schedule("STOCK_PROGRAM_1MIN")
        execution = self.codes.api_schedule("STOCK_EXECUTION_5SEC")
        return collection_window_active(now) and now.second in (
            *SCHEDULED_SNAPSHOT_SECONDS,
            1,
            program.execution_second if program.due(now) else -1,
            now.second if execution.due(now) else -1,
        )

    def collect_tick(self, *, now: datetime, store_records: bool = False) -> RawCollectionTick:
        """Collect one scheduled tick with per-stock failure isolation.

        The branch order intentionally mirrors the active runner: a due program
        request owns that stock's tick and skips the minute/snapshot request.
        Execution strength remains the existing single configured instrument
        contract; no additional execution target is introduced here.
        """
        result = RawCollectionTick(observed_at=now)
        if not self.scheduled(now):
            result.skipped = True
            return result

        program_schedule = self.codes.api_schedule("STOCK_PROGRAM_1MIN")
        execution_schedule = self.codes.api_schedule("STOCK_EXECUTION_5SEC")
        program_due = program_schedule.due(now)
        execution_due = execution_schedule.due(now)

        for stock in self.codes.enabled_minute_stocks():
            venue = stock.default_market_code
            if program_due and stock.analysis_yn and stock.program_collect_yn:
                self._collect_program(result, stock.stock_code, venue, store_records)
                continue
            if execution_due and stock.stock_code == self.execution_stock_code and stock.analysis_yn:
                self._collect_execution(result, stock.stock_code, venue, store_records)
            if now.second not in (*SCHEDULED_SNAPSHOT_SECONDS, 1):
                continue
            self._collect_minute_or_snapshot(result, stock.stock_code, venue, now, store_records)
        return result

    def _append(
        self,
        result: RawCollectionTick,
        table: RawTable,
        stock_code: str,
        venue: str,
        rows,
        store_records: bool,
        *,
        scheduled_at: datetime,
        requested_at: datetime,
        response_received_at: datetime,
    ) -> None:
        normalized = list(rows) if isinstance(rows, list) else [rows]
        canonical_ready_at = datetime.now(tz=scheduled_at.tzinfo)
        for row in normalized:
            result.records.append(RawCollectionRecord(
                table, stock_code, venue, row, scheduled_at, requested_at,
                response_received_at, canonical_ready_at,
            ))
        if store_records and normalized:
            self.raw_ingestion.store(table, normalized)

    def _failure(self, result: RawCollectionTick, table: RawTable, stock_code: str, venue: str, error: Exception, *, scheduled_at: datetime, requested_at: datetime | None = None) -> None:
        result.failures.append(RawCollectionFailure(table, stock_code, venue, type(error).__name__, scheduled_at, requested_at))
        self.log(f"KIS {table.value} collection failed stock={stock_code} error={type(error).__name__}")

    def _collect_program(self, result: RawCollectionTick, stock_code: str, venue: str, store_records: bool) -> None:
        scheduled_at = result.observed_at
        requested_at = datetime.now(tz=scheduled_at.tzinfo)
        try:
            self._inject(RawTable.PROGRAM, stock_code, venue)
            rows = self.program_collector.collect(stock_code=stock_code, market_code="KOSPI", trading_venue=venue)
            self._append(result, RawTable.PROGRAM, stock_code, venue, rows, store_records,
                         scheduled_at=scheduled_at, requested_at=requested_at,
                         response_received_at=datetime.now(tz=scheduled_at.tzinfo))
        except Exception as error:
            self._failure(result, RawTable.PROGRAM, stock_code, venue, error, scheduled_at=scheduled_at, requested_at=requested_at)

    def _collect_execution(self, result: RawCollectionTick, stock_code: str, venue: str, store_records: bool) -> None:
        scheduled_at = result.observed_at
        requested_at = datetime.now(tz=scheduled_at.tzinfo)
        try:
            self._inject(RawTable.STOCK_EXECUTION, stock_code, venue)
            rows = self.execution_collector.collect(stock_code=stock_code, market_code="KOSPI", trading_venue=venue, collect_cycle="5SEC")
            self._append(result, RawTable.STOCK_EXECUTION, stock_code, venue, rows, store_records,
                         scheduled_at=scheduled_at, requested_at=requested_at,
                         response_received_at=datetime.now(tz=scheduled_at.tzinfo))
        except Exception as error:
            self._failure(result, RawTable.STOCK_EXECUTION, stock_code, venue, error, scheduled_at=scheduled_at, requested_at=requested_at)

    def _collect_minute_or_snapshot(self, result: RawCollectionTick, stock_code: str, venue: str, now: datetime, store_records: bool) -> None:
        requested_at = datetime.now(tz=now.tzinfo)
        try:
            self._inject(RawTable.STOCK_MINUTE, stock_code, venue)
            rows = self.minute_collector.collect(stock_code=stock_code, market_code="KOSPI", trading_venue=venue, input_hour=now.strftime("%H%M%S"))
            response_received_at = datetime.now(tz=now.tzinfo)
        except Exception as error:
            self._failure(result, RawTable.STOCK_MINUTE, stock_code, venue, error, scheduled_at=now, requested_at=requested_at)
            return
        if now.second == 1:
            row = completed_minute_row(rows, now)
            if row is not None:
                self._append(result, RawTable.STOCK_MINUTE, stock_code, venue, row, store_records,
                             scheduled_at=now, requested_at=requested_at, response_received_at=response_received_at)
            return
        snapshot = StockMinuteSnapshotService.build_snapshot(collector_rows=rows, observed_at=now)
        if snapshot is not None:
            self._append(result, RawTable.STOCK_MINUTE_SNAPSHOT, stock_code, venue, snapshot, store_records,
                         scheduled_at=now, requested_at=requested_at, response_received_at=response_received_at)

    def _inject(self, table: RawTable, stock_code: str, venue: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(table, stock_code, venue)
