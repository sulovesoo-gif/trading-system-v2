"""Trade-specific V1 STOP evaluation for LIVE/NO_SEND orchestration."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from .engine import SignalEvent, SignalType
from .v1_policy import stop_event_key


@dataclass(frozen=True)
class V1LiveOpenTrade:
    minute_live_trade_id: int
    minute_policy_path_id: int
    ownership_id: str
    underlying_entry_reference_price: Decimal
    entry_execution_time: datetime


class MinuteMaV1LiveStopMonitor:
    """A STOP targets one live_trade/ownership and never the whole path."""

    def __init__(self, *, repository, planner, price_lookup) -> None:
        self.repository=repository;self.planner=planner;self.price_lookup=price_lookup

    def evaluate_completed_bar(self, *, path, bar) -> dict[str,int]:
        counts: dict[str,int]={}
        close=Decimal(str(bar.close_price))
        for trade in self.repository.v1_live_open_trades(path=path):
            if trade.entry_execution_time > bar.bar_time:
                continue
            if not path.operation_policy.stop_triggered(
                    anchor=trade.underlying_entry_reference_price,
                    completed_underlying_close=close):
                continue
            key=stop_event_key(policy_path_id=trade.minute_policy_path_id,
                               trade_id=trade.minute_live_trade_id,
                               trigger_bar_time=bar.bar_time)
            event=SignalEvent(path.minute_path_id,path.path_key,SignalType.EXIT,bar.bar_time,
                              bar.bar_time+timedelta(minutes=1,seconds=1),key,True,{}, {})
            status=self.planner.plan_trade_exit(
                path=path,event=event,
                reference_price=self.price_lookup.current_price(path.execution_code),
                minute_live_trade_id=trade.minute_live_trade_id,exit_reason='STOP_EXIT')
            counts[status]=counts.get(status,0)+1
        return counts


class MinuteMaV1LiveRuntime:
    """Production V1 signal orchestration; it intentionally has no EOD path."""

    def __init__(self, *, repository, planner, price_lookup, cash_lookup, engine=None) -> None:
        from .engine import MinuteMaSignalEngine
        self.repository=repository;self.planner=planner;self.price_lookup=price_lookup
        self.cash_lookup=cash_lookup;self.engine=engine or MinuteMaSignalEngine()

    def run_day(self, *, trading_date: date) -> dict[str,int]:
        paths=self.repository.v1_policy_paths(live_only=True)
        groups=defaultdict(list);counts=defaultdict(int)
        for path in paths:groups[path.signal_code].append(path)
        for signal_code,group in groups.items():
            points=self.engine.prepare(path=group[0],bars=self.repository.source_bars(
                stock_code=signal_code,axis=group[0].axis,trading_date=trading_date))
            cursor=self.repository.v1_live_runtime_cursor(signal_code=signal_code)
            if cursor is None:
                if points:self.repository.advance_v1_live_cursor(
                    signal_code=signal_code,last_source_bar_time=points[-1].bar_time)
                counts['BOOTSTRAPPED_NO_REPLAY']+=1;continue
            points=tuple(point for point in points if point.bar_time>cursor)
            events_by_time=defaultdict(list)
            for path in group:
                for event in self.engine.evaluate_prepared(path=path,points=points):
                    if event.source_bar_time.date()==trading_date:
                        events_by_time[event.source_bar_time].append((path,event))
            stop_monitor=MinuteMaV1LiveStopMonitor(
                repository=self.repository,planner=self.planner,price_lookup=self.price_lookup)
            for point in points:
                bar=type('CompletedBar',(),{'bar_time':point.bar_time,'close_price':point.source_close})()
                for path in group:
                    for status,n in stop_monitor.evaluate_completed_bar(path=path,bar=bar).items():
                        counts[status]+=n
                for path,event in sorted(events_by_time.get(point.bar_time,()),
                    key=lambda x:0 if x[1].signal_type is SignalType.EXIT else 1):
                    reference=self.price_lookup.current_price(path.execution_code)
                    if event.signal_type is SignalType.ENTRY:
                        if not path.operation_policy.allows_entry(event.source_bar_time,live=True):continue
                        anchor=self.price_lookup.minute_open(
                            path.signal_code,event.confirmed_at.replace(second=0,microsecond=0))
                        cash=self.cash_lookup.orderable_cash(
                            stock_code=path.execution_code,order_price=reference,order_division='01')
                        status=self.planner.plan_entry(path=path,event=event,reference_price=reference,
                            available_cash=cash.amount,underlying_entry_reference_price=anchor)
                        counts[status]+=1
                    else:
                        for trade in self.repository.v1_live_open_trades(path=path):
                            status=self.planner.plan_trade_exit(path=path,event=event,
                                reference_price=reference,minute_live_trade_id=trade.minute_live_trade_id,
                                exit_reason='NORMAL_EXIT')
                            counts[status]+=1
            if points:self.repository.advance_v1_live_cursor(
                signal_code=signal_code,last_source_bar_time=points[-1].bar_time)
        return dict(counts)
