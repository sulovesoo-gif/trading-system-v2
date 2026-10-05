"""Five-second actual-position protection; no BUY, history polling or market edits."""
import logging
from decimal import Decimal
from time import monotonic

LOGGER=logging.getLogger(__name__)
POLL_SECONDS=5.0


class ActualStopProtection:
    def __init__(self,*,planner,price_lookup,submitter,clock,session_open):
        self.planner,self.price_lookup,self.submitter=planner,price_lookup,submitter
        self.clock,self.session_open=clock,session_open

    def poll(self):
        stocks=self.planner.protection_stocks()
        if not stocks or not self.session_open(self.clock()):return 0
        planned=0
        for stock in stocks:
            try:
                # Existing FHKST01010100 KRX current-price adapter; no cached fallback.
                price=Decimal(str(self.price_lookup.current_price(stock)))
                if not price.is_finite() or price<=0:raise ValueError('INVALID_PROTECTION_PRICE')
                observed=self.clock()
                planned+=self.planner.plan_exits(at=observed,protection={stock:(price,observed)})
                for key in self.planner.protection_request_keys(stock):
                    self.submitter.process_request(key)
            except Exception as error:
                LOGGER.exception('FIRST_RISE_PROTECTION_POLL_ERROR stock_code=%s exception_type=%s',
                                 stock,type(error).__name__)
        return planned

    def run(self,stop):
        # No overlapping polls or catch-up bursts if REST/DB takes >5 seconds.
        while not stop.is_set():
            began=monotonic()
            try:self.poll()
            except Exception:LOGGER.exception('FIRST_RISE_PROTECTION_CYCLE_ERROR')
            elapsed=monotonic()-began
            if elapsed>POLL_SECONDS:
                LOGGER.warning('FIRST_RISE_PROTECTION_LAG elapsed_ms=%d',int(elapsed*1000))
            stop.wait(max(0,POLL_SECONDS-elapsed))
