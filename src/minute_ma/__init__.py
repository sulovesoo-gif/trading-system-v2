"""Four-axis intraday MA research/PAPER/LIVE front-end.

The package owns signal semantics and path lifecycle only. Broker transport,
fills, ownership, reconciliation, costs and settlement remain shared services.
"""

from .contracts import Axis, ContinuityMode, MarketSource, MinuteBar, MinuteMaPath
from .engine import MinuteMaSignalEngine, SignalEvent, SignalType

__all__ = [
    "Axis", "ContinuityMode", "MarketSource", "MinuteBar", "MinuteMaPath",
    "MinuteMaSignalEngine", "SignalEvent", "SignalType",
]

