"""Separate execution-cycle wiring. No signal generation or collector ownership."""
import logging
LOGGER=logging.getLogger(__name__)


class JLiveRuntime:
    def __init__(self,*,context,planner,submit_store,submitter,recovery,price_lookup,cash_lookup,cost_finalizer,cancellations=None,capacity_monitor=None,trading_day=None):
        self.context,self.planner=context,planner
        self.submit_store,self.submitter=submit_store,submitter
        self.recovery,self.price_lookup,self.cash_lookup=recovery,price_lookup,cash_lookup
        self.cost_finalizer=cost_finalizer
        self.cancellations=cancellations
        self.capacity_monitor=capacity_monitor
        self.trading_day=trading_day

    def submit_ready(self):
        result={}
        for key in self.submit_store.discover_ready_request_keys():
            try:
                _,status=self.submitter.process_request(key)
                result[status]=result.get(status,0)+1
            except Exception as error:
                # Durable SUBMITTING/POST attempt is never reset for retry.
                LOGGER.exception('FIRST_RISE_SUBMIT_ERROR request_key=%s exception_type=%s',key,type(error).__name__)
                result['errors']=result.get('errors',0)+1
        return result

    def cycle(self,*,at):
        entry_day=self.trading_day is None or self.trading_day(at)
        self.context.load(at=at)
        recovered=self.recovery.poll(at=at)
        if self.cancellations:
            self.cancellations.cycle(at=at)
            self.recovery.poll(at=at) # terminal history/checkpoint, never cancel ACK alone
        exits=self.planner.plan_exits(at=at)
        submitted=self.submit_ready()
        planned=[]
        if entry_day and self.context.config is not None:
            for signal in self.planner.entry_signals(at=at):
                try:
                    planned.append(self.planner.plan_buy(signal,context=self.context,at=at,
                        price_lookup=self.price_lookup,cash_lookup=self.cash_lookup))
                except Exception:
                    LOGGER.exception('FIRST_RISE_PLAN_ERROR market_signal_id=%s',signal)
                    planned.append('ERROR')
                # Fresh KIS cash on each next signal; ACK state serializes with sizing.
                for status,count in self.submit_ready().items():submitted[status]=submitted.get(status,0)+count
        costs=self.cost_finalizer.finalize_due(today=at.date())
        if self.capacity_monitor is not None:
            try:self.capacity_monitor.refresh(at=at)
            except Exception:LOGGER.exception('FIRST_RISE_CAPACITY_MONITOR_ERROR')
        return dict(recovery=recovered,exit_requests=exits,planning=planned,submitted=submitted,costs=costs)
