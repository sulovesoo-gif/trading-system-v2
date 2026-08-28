from __future__ import annotations
from collections import defaultdict
from datetime import date
from .engine import MinuteMaSignalEngine,SignalType
from .engine import SignalEvent
from hashlib import sha256
from datetime import datetime,time,timedelta

class MinuteMaLiveSignalRuntime:
    """Evaluate completed source bars without waiting for the PAPER proxy bar."""
    def __init__(self,*,repository,planner,price_lookup,cash_lookup):
        self.repository,self.planner=repository,planner
        self.price_lookup,self.cash_lookup=price_lookup,cash_lookup
        self.engine=MinuteMaSignalEngine()
    def run_axis(self,*,trading_date:date,axis):
        paths=self.repository.live_paths(axis);by_signal=defaultdict(list)
        for p in paths:by_signal[p.signal_code].append(p)
        counts=defaultdict(int)
        for signal,group in by_signal.items():
            points=self.engine.prepare(path=group[0],bars=self.repository.source_bars(stock_code=signal,axis=axis,trading_date=trading_date))
            cursor=self.repository.live_runtime_cursor(axis=axis,signal_code=signal)
            # First production start establishes a durable watermark. It must
            # never replay old intraday signals into real orders.
            if cursor is None:
                if points:self.repository.advance_live_cursor(axis=axis,signal_code=signal,last_source_bar_time=points[-1].bar_time)
                counts['BOOTSTRAPPED_NO_REPLAY']+=1
                continue
            if cursor is not None:points=tuple(x for x in points if x.bar_time>cursor)
            for path in group:
                events=[e for e in self.engine.evaluate_prepared(path=path,points=points) if e.source_bar_time.date()==trading_date]
                events.sort(key=lambda e:(e.source_bar_time,0 if e.signal_type is SignalType.EXIT else 1))
                for event in events:
                    if event.signal_type is SignalType.ENTRY and not path.axis.allows_entry_source_time(event.source_bar_time.time()):continue
                    price=self.price_lookup.current_price(path.execution_code)
                    if event.signal_type is SignalType.ENTRY:
                        cash=self.cash_lookup.orderable_cash(stock_code=path.execution_code,order_price=price,order_division='01')
                        status=self.planner.plan_entry(path=path,event=event,reference_price=price,available_cash=cash.amount)
                        counts[status]+=1
                    else:
                        for status in self.planner.plan_exit(path=path,event=event,reference_price=price):counts[status]+=1
            if points:self.repository.advance_live_cursor(axis=axis,signal_code=signal,last_source_bar_time=points[-1].bar_time)
        return dict(counts)

    def plan_eod(self,*,trading_date:date,axis,now:datetime):
        if now.time()<time(15,19):return {}
        counts=defaultdict(int);source=datetime.combine(trading_date,time(15,19))
        for path in self.repository.live_paths(axis):
            key=sha256(f'MINUTE_MA_V01|{path.path_key}|EOD_EXIT|{source.isoformat()}'.encode()).hexdigest()
            event=SignalEvent(path.minute_path_id,path.path_key,SignalType.EXIT,source,source+timedelta(seconds=1),key,True,{}, {})
            price=self.price_lookup.current_price(path.execution_code)
            for status in self.planner.plan_exit(path=path,event=event,reference_price=price,exit_reason='EOD_1519'):
                counts[status]+=1
        return dict(counts)
