"""Independent market evidence watch; actual order/PAPER outcomes are not inputs.

The J runtime consumer uses this watch policy even when its actual execution
layer skips/rejects an entry. It must not use legacy candidate terminal states
as the market lifecycle's terminal state.
"""
from datetime import time


def market_needs_minutes(state, *, at, cutoff):
    if at.date() != state.tracking.business_date:
        return False
    if state.open_signal is not None:
        return True  # Include the final SESSION_CLOSE evaluation after 15:30.
    if at.time() >= cutoff:
        return False
    if state.sequence == 0:
        return True
    return state.sequence == 1 and state.prior_exit_reason == 'STOP_ENTRY_BREAK'


def completed_market_bars(source, state, *, at, cutoff):
    """All received completed bars are committed by the source before return."""
    if not market_needs_minutes(state,at=at,cutoff=cutoff):
        source.discard(stock_code=state.tracking.stock_code,business_date=state.tracking.business_date)
        return []
    return source.completed_bars_from_open(stock_code=state.tracking.stock_code,as_of=at)
