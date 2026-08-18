"""Frozen LIVE Champion registry definitions.

These definitions deliberately contain identity and grouping only.  They do
not enable a runtime, create an order, or reinterpret the frozen strategies.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FrozenLiveChampion:
    strategy_id: int
    live_name: str
    entry_group: str | None = None
    initial_live_capital: Decimal = Decimal("1000000")


FROZEN_LIVE_CHAMPIONS: tuple[FrozenLiveChampion, ...] = (
    FrozenLiveChampion(294, "LIVE_HYNIX_S3_3BAR", "HYNIX_S3_SHARED_ENTRY"),
    FrozenLiveChampion(299, "LIVE_HYNIX_S3_5BAR", "HYNIX_S3_SHARED_ENTRY"),
    FrozenLiveChampion(802, "LIVE_SAMSUNG_S1_LONG"),
    FrozenLiveChampion(623, "LIVE_SAMSUNG_S2_SHORT"),
)
