from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


class ResearchState(str, Enum):
    DISCOVERED = "DISCOVERED"
    TRACKING = "TRACKING"
    PULLBACK = "PULLBACK"
    WAIT_REBREAK = "WAIT_REBREAK"
    PAPER_ENTERED = "PAPER_ENTERED"
    PAPER_EXITED = "PAPER_EXITED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


TERMINAL_STATES = {ResearchState.PAPER_EXITED, ResearchState.REJECTED, ResearchState.EXPIRED}


@dataclass(frozen=True)
class CandidateState:
    candidate_event_id: UUID
    business_date: date
    stock_code: str
    state: ResearchState
    peak_price: Decimal | None = None
    peak_time: datetime | None = None
    pullback_low_price: Decimal | None = None
    pullback_pct: Decimal | None = None
    last_observed_at: datetime | None = None
    last_observed_price: Decimal | None = None
    entry_event_key: str | None = None
    entry_signal_time: datetime | None = None
    raw_entry_price: Decimal | None = None
    entry_execution_price: Decimal | None = None
    version: int = 0

    def evolve(self, **changes: Any) -> "CandidateState":
        return replace(self, **changes, version=self.version + 1)


@dataclass(frozen=True)
class Observation:
    observed_at: datetime
    price: Decimal
    source: str = "H0STCNT0"


@dataclass(frozen=True)
class MinuteBar:
    bar_time: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    volume: int = 0
    accumulated_amount: Decimal | None = None
    source: str = "KIS_1MIN"

    @classmethod
    def from_mapping(cls, row: dict[str, Any], *, source: str = "KIS_1MIN") -> "MinuteBar":
        return cls(
            bar_time=row["bar_time"],
            open_price=Decimal(row["open_price"]),
            high_price=Decimal(row["high_price"]),
            low_price=Decimal(row["low_price"]),
            close_price=Decimal(row["close_price"]),
            volume=int(row.get("volume") or 0),
            accumulated_amount=(
                Decimal(row["accumulated_amount"])
                if row.get("accumulated_amount") is not None else None
            ),
            source=source,
        )


@dataclass(frozen=True)
class Decision:
    before: CandidateState
    after: CandidateState
    reason: str
    create_entry: bool = False
    create_exit: bool = False
    signal_time: datetime | None = None
    raw_execution_price: Decimal | None = None
    evidence: dict[str, Any] | None = None

    @property
    def changed(self) -> bool:
        return self.before != self.after
