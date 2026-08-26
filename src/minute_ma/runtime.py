from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, time, timedelta

from .contracts import Axis
from .engine import MinuteMaSignalEngine, SignalType


@dataclass(frozen=True)
class RuntimeResult:
    paths_evaluated: int
    events_seen: int
    trades_created: int
    trades_closed: int
    non_executable_events: int


class MinuteMaPaperRuntime:
    def __init__(self, repository, *, engine: MinuteMaSignalEngine | None = None) -> None:
        self.repository = repository
        self.engine = engine or MinuteMaSignalEngine()

    def run_day(self, *, trading_date: date, axis: Axis) -> RuntimeResult:
        paths = self.repository.paths(axis)
        by_signal: dict[str,list] = defaultdict(list)
        for path in paths:
            by_signal[path.signal_code].append(path)
        event_count=created=closed=non_executable=0
        for signal_code,signal_paths in by_signal.items():
            bars = self.repository.source_bars(stock_code=signal_code,axis=axis,trading_date=trading_date)
            prepared=self.engine.prepare(path=signal_paths[0],bars=bars)
            cursor=self.repository.runtime_cursor(axis=axis,signal_code=signal_code)
            execution_codes={path.execution_code for path in signal_paths}
            watermarks=[self.repository.execution_watermark(stock_code=code,trading_date=trading_date)
                        for code in execution_codes]
            executable_watermark=min((value for value in watermarks if value is not None),default=None)
            safe_points=[]
            for point in prepared:
                proxy_time=point.bar_time+timedelta(minutes=1)
                outside=not time(9,0)<=proxy_time.time()<=time(15,19)
                if outside or (executable_watermark is not None and proxy_time<=executable_watermark):
                    safe_points.append(point)
            if cursor is not None:
                safe_points=[point for point in safe_points if point.bar_time>cursor]
            for path in signal_paths:
                events = [e for e in self.engine.evaluate_prepared(path=path,points=safe_points)
                          if e.source_bar_time.date()==trading_date]
                # Existing exits settle first; same-bar new entries remain independent.
                events.sort(key=lambda e:(e.source_bar_time,0 if e.signal_type is SignalType.EXIT else 1))
                for event in events:
                    event_count += 1
                    proxy_time = event.source_bar_time+timedelta(minutes=1)
                    if event.signal_type is SignalType.ENTRY and not time(9,0) <= proxy_time.time() <= time(15,19):
                        self.repository.record_non_executable(event,status="OUTSIDE_KRX_EXECUTION_WINDOW")
                        non_executable += 1
                        continue
                    proxy = self.repository.execution_bar(stock_code=path.execution_code,at=proxy_time)
                    if proxy is None:
                        self.repository.record_non_executable(event,status="NO_PROXY_BAR")
                        non_executable += 1
                        continue
                    c,x = self.repository.apply_event(path=path,event=event,proxy_bar=proxy)
                    created += c; closed += x
                closed += self.repository.close_eod(path=path,trading_date=trading_date)
            if safe_points:
                self.repository.advance_cursor(axis=axis,signal_code=signal_code,
                                               last_source_bar_time=safe_points[-1].bar_time)
        return RuntimeResult(len(paths),event_count,created,closed,non_executable)
