"""Daily MA SEND orchestration boundary; test transport is injectable."""
from __future__ import annotations
from .actual_submit import DailyMaDurableSubmitService

class DailyMaSendOrchestrator:
    """Durable request claim is always before the single permitted submit."""
    def __init__(self, *, submit_store, submit_runtime):
        self.submit = DailyMaDurableSubmitService(store=submit_store, runtime=submit_runtime)

    def process_request(self, request_key):
        return self.submit.submit_request(request_key)

    def recover_unknown(self, *, order, runtime, history_lookup, order_date):
        return runtime.recover_unknown(order=order, history_lookup=history_lookup, order_date=order_date)
