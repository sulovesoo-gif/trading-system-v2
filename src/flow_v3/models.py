"""Value objects shared by the FLOW V3 runtime and persistence adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class StrategyContract:
    strategy_id: str
    stock_code: str
    direction: str
    entry_family_code: str
    entry_fast_period: int
    entry_slow_period: int
    program_condition_code: str
    exit_fast_period: int
    exit_slow_period: int
    exit_policy_code: str
    execution_code: str


@dataclass(frozen=True)
class MinuteBase:
    business_date: date
    stock_code: str
    bar_time: datetime
    underlying_close: Decimal
    aggressive_buy_amount: Decimal
    aggressive_sell_amount: Decimal
    flow_value: Decimal
    program_net_flow: Decimal | None
    bid_start: Decimal | None = None
    bid_end: Decimal | None = None
    ask_start: Decimal | None = None
    ask_end: Decimal | None = None
    snapshot_count: int = 0
    execution_source_gap: bool = False
    execution_duplicate_rows: int = 0
    program_source_gap: bool = False
    program_duplicate_rows: int = 0
    orderbook_source_gap: bool = False
    orderbook_duplicate_rows: int = 0


@dataclass(frozen=True)
class MinuteState:
    business_date: date
    stock_code: str
    bar_time: datetime
    underlying_close: Decimal
    aggressive_buy_amount: Decimal
    aggressive_sell_amount: Decimal
    flow_value: Decimal
    velocity_value: Decimal | None
    program_net_flow: Decimal | None
    program_velocity: Decimal | None
    long_absorption: bool
    short_absorption: bool
    snapshot_count: int
    execution_source_gap: bool
    execution_duplicate_rows: int
    program_source_gap: bool
    program_duplicate_rows: int
    orderbook_source_gap: bool
    orderbook_duplicate_rows: int
    quality_code: str
    flow_averages: dict[int, Decimal | None] = field(default_factory=dict)
    velocity_averages: dict[int, Decimal | None] = field(default_factory=dict)
    flow_crosses: dict[str, int | None] = field(default_factory=dict)
    velocity_crosses: dict[str, int | None] = field(default_factory=dict)

    @property
    def is_complete(self) -> bool:
        return self.quality_code == "OK"


@dataclass(frozen=True)
class EntrySignal:
    strategy: StrategyContract
    state: MinuteState
    entry_event_key: str
    velocity_lead_time: datetime | None = None
