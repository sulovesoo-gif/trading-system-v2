"""Completed-minute eligibility shared by realtime collection and backfill.

The policy intentionally answers only whether a KIS response may be persisted
as a regular-session completed bar.  It does not fabricate missing minutes or
classify a zero-volume bar as invalid; a zero-volume minute can be legitimate.
"""

from __future__ import annotations

from datetime import datetime, time


_REGULAR_SESSION: dict[str, tuple[time, time]] = {
    # Historical normal KRX RAW contains legitimate 15:30 bars.  The collector
    # may therefore request that final completed minute, but never 15:31+.
    "KRX": (time(9, 0), time(15, 30)),
    # These bounds follow the existing MARKET common-code operating contract.
    "NXT": (time(8, 0), time(20, 0)),
    "INTEGRATED": (time(8, 0), time(20, 0)),
}


def is_regular_completed_minute(*, trading_venue: str, bar_time: datetime) -> bool:
    """Return whether ``bar_time`` belongs to the venue's persisted session."""
    try:
        start, end = _REGULAR_SESSION[trading_venue]
    except KeyError as error:
        raise ValueError(f"unsupported trading venue: {trading_venue}") from error
    return bar_time.weekday() < 5 and start <= bar_time.time() <= end
