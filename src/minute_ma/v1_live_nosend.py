"""V1 policy bridge into the existing durable Minute-MA NO_SEND adapter."""
from __future__ import annotations

from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal

from .engine import MinuteMaSignalEngine,SignalEvent,SignalType
from .v1_policy import stop_event_key


class MinuteMaV1LiveNoSendRuntime:
    def __init__(self,*,repository,adapter,execution_price_lookup,underlying_price_lookup,
                 cash_lookup,cash_includes_pending_reservations:bool=True):
        self.repository=repository;self.adapter=adapter
        self.execution_price_lookup=execution_price_lookup
        self.underlying_price_lookup=underlying_price_lookup;self.cash_lookup=cash_lookup
        self.cash_includes_pending_reservations=cash_includes_pending_reservations
        self.engine=MinuteMaSignalEngine()

    def run_day(self,*,trading_date:date)->dict[str,int]:
        paths=self.repository.v1_policy_paths(live_only=True);groups=defaultdict(list);counts=defaultdict(int)
        for path in paths:groups[path.signal_code].append(path)
        for signal_code,group in groups.items():
            points=self.engine.prepare(path=group[0],bars=self.repository.v1_source_bars(
                stock_code=signal_code,trading_date=trading_date))
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
            for point in points:
                for path in group:self._stops(path=path,point=point,counts=counts)
                for path,event in sorted(events_by_time.get(point.bar_time,()),
                    key=lambda x:0 if x[1].signal_type is SignalType.EXIT else 1):
                    if event.signal_type is SignalType.ENTRY:
                        if not path.operation_policy.allows_entry(event.source_bar_time,live=True):continue
                        execution_price=Decimal(self.execution_price_lookup.current_price(path.execution_code))
                        anchor=Decimal(self.underlying_price_lookup.minute_open(
                            path.signal_code,event.confirmed_at.replace(second=0,microsecond=0)))
                        cash=self.cash_lookup.orderable_cash(stock_code=path.execution_code,
                                                             order_price=execution_price,order_division='01')
                        result=self.adapter.plan_entry(
                            minute_path_id=path.minute_path_id,minute_paper_trade_id=None,
                            signal_event_key=event.signal_event_key,execution_stock_code=path.execution_code,
                            reference_price=execution_price,available_cash=cash.amount,
                            cash_includes_pending_reservations=self.cash_includes_pending_reservations,
                            source_event_time=event.confirmed_at,minute_policy_path_id=path.minute_policy_path_id,
                            underlying_entry_reference_price=anchor,
                            stop_threshold_price=path.operation_policy.threshold(anchor),
                            stop_policy=('UNDERLYING_1PCT' if path.direction=='SHORT' else 'UNDERLYING_5PCT'))
                        counts[result.status]+=1
                    else:
                        for trade in self.repository.v1_live_open_trades(path=path):
                            result=self.adapter.plan_exit(
                                minute_live_trade_id=trade.minute_live_trade_id,
                                execution_stock_code=path.execution_code,
                                reference_price=Decimal(self.execution_price_lookup.current_price(path.execution_code)),
                                source_event_time=event.confirmed_at,exit_reason='NORMAL_EXIT',
                                minute_policy_path_id=path.minute_policy_path_id)
                            counts[result.status]+=1
            if points:self.repository.advance_v1_live_cursor(
                signal_code=signal_code,last_source_bar_time=points[-1].bar_time)
        return dict(counts)

    def _stops(self,*,path,point,counts):
        current=Decimal(str(point.source_close))
        for trade in self.repository.v1_live_open_trades(path=path):
            if trade.entry_execution_time>point.bar_time or not path.operation_policy.stop_triggered(
                anchor=trade.underlying_entry_reference_price,completed_underlying_close=current):continue
            key=stop_event_key(policy_path_id=trade.minute_policy_path_id,
                               trade_id=trade.minute_live_trade_id,trigger_bar_time=point.bar_time)
            result=self.adapter.plan_exit(
                minute_live_trade_id=trade.minute_live_trade_id,
                execution_stock_code=path.execution_code,
                reference_price=Decimal(self.execution_price_lookup.current_price(path.execution_code)),
                source_event_time=point.finalized_at or point.bar_time+timedelta(minutes=1,seconds=1),
                exit_reason='STOP_EXIT',minute_policy_path_id=path.minute_policy_path_id)
            counts[result.status]+=1
