"""Bounded EOD priority pass; no broker client/config creation here."""
from datetime import datetime, time


def eod_priority(now):
    return time(15,18)<=now.time()<time(15,20)


def wait_seconds(now):
    if eod_priority(now):
        return 1.0
    normal=10.0 if 9<=now.hour<16 else 300.0
    boundary=datetime.combine(now.date(),time(15,18))
    return min(normal,(boundary-now).total_seconds()) if now<boundary else normal


def priority_exits(repository,reader,transport,clock):
    """Create release -> terminal history -> quote -> SELL, in the SAME pass.

    Two bounded passes permit cancel ACK -> final history -> actual SELL quantity.
    Missing history/quote/cursor stays pending; never fake a fill or extend deadline.
    """
    result=dict(priority_posts=0,priority_polled=0)
    if not eod_priority(clock()): return result
    repository.cycle(clock(),{},exits_only=True)
    for _ in range(2):
        if not eod_priority(clock()): break
        result['priority_polled']+=reader.poll(repository,exits_only=True)
        quotes=reader.quotes(repository.quote_codes())
        repository.cycle(clock(),quotes,exits_only=True)
        result['priority_posts']+=transport.run(exits_only=True)
    return result
