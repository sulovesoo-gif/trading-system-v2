"""Fixed RAW table specifications derived from the approved DDL files."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RawTable(str, Enum):
    PROGRAM = "raw_program"
    MARKET_INVESTOR = "raw_market_investor"
    STOCK_QUOTE = "raw_stock_quote"
    STOCK_EXECUTION = "raw_stock_execution"
    STOCK_MINUTE = "raw_stock_minute"
    STOCK_DAILY = "raw_stock_daily"
    FUTURES_QUOTE = "raw_futures_quote"
    FUTURES_MINUTE = "raw_futures_minute"


@dataclass(frozen=True)
class RawTableSpec:
    table: RawTable
    ddl_file: str
    columns: tuple[str, ...]  # created_at is intentionally omitted: DB DEFAULT owns it.
    primary_key: tuple[str, ...]


COMMON_SNAPSHOT = ("snapshot_time", "collected_at", "data_source", "market_code", "collect_cycle")
COMMON_BAR = ("bar_time", "collected_at", "data_source", "market_code", "collect_cycle")
VENUE_SNAPSHOT = ("snapshot_time", "collected_at", "data_source", "market_code", "trading_venue", "collect_cycle")
VENUE_BAR = ("bar_time", "collected_at", "data_source", "market_code", "trading_venue", "collect_cycle")
INVESTORS = ("foreign", "individual", "institution", "financial_investment", "investment_trust", "private_fund", "bank", "insurance", "merchant_bank", "fund", "other_organization", "other_corporation")
INVESTOR_VALUES = tuple(f"{investor}_{field}" for investor in INVESTORS for field in ("sell_volume", "buy_volume", "net_buy_volume", "sell_amount", "buy_amount", "net_buy_amount"))


RAW_SPECS: dict[RawTable, RawTableSpec] = {
    RawTable.PROGRAM: RawTableSpec(RawTable.PROGRAM, "10_raw_program.sql", COMMON_SNAPSHOT + ("stock_code", "current_price", "previous_day_difference", "previous_day_difference_sign", "change_rate", "accumulated_volume", "sell_volume", "buy_volume", "net_buy_volume", "sell_amount", "buy_amount", "net_buy_amount", "net_buy_volume_change", "net_buy_amount_change", "raw_payload"), ("snapshot_time", "data_source", "market_code", "collect_cycle", "stock_code")),
    RawTable.MARKET_INVESTOR: RawTableSpec(RawTable.MARKET_INVESTOR, "11_raw_market_investor.sql", COMMON_SNAPSHOT + INVESTOR_VALUES + ("raw_payload",), ("snapshot_time", "data_source", "market_code", "collect_cycle")),
    RawTable.STOCK_QUOTE: RawTableSpec(RawTable.STOCK_QUOTE, "12_raw_stock_quote.sql", VENUE_SNAPSHOT + ("stock_code", "current_price", "previous_day_difference", "previous_day_difference_sign", "change_rate", "open_price", "high_price", "low_price", "base_price", "upper_limit_price", "lower_limit_price", "accumulated_volume", "accumulated_amount", "weighted_average_price", "foreign_net_buy_volume", "program_net_buy_volume", "vi_classification_code", "trading_halt_yn", "raw_payload"), ("snapshot_time", "data_source", "market_code", "trading_venue", "collect_cycle", "stock_code")),
    RawTable.STOCK_EXECUTION: RawTableSpec(RawTable.STOCK_EXECUTION, "13_raw_stock_execution.sql", VENUE_SNAPSHOT + ("stock_code", "current_price", "previous_day_difference", "previous_day_difference_sign", "change_rate", "execution_volume", "execution_strength", "raw_payload"), ("snapshot_time", "data_source", "market_code", "trading_venue", "collect_cycle", "stock_code")),
    RawTable.STOCK_MINUTE: RawTableSpec(RawTable.STOCK_MINUTE, "14_raw_stock_minute.sql", VENUE_BAR + ("stock_code", "open_price", "high_price", "low_price", "close_price", "volume", "accumulated_amount", "raw_payload"), ("bar_time", "data_source", "market_code", "trading_venue", "collect_cycle", "stock_code")),
    RawTable.STOCK_DAILY: RawTableSpec(RawTable.STOCK_DAILY, "15_raw_stock_daily.sql", ("trade_date", "collected_at", "data_source", "market_code", "trading_venue", "collect_cycle", "stock_code", "open_price", "high_price", "low_price", "close_price", "volume", "amount", "previous_day_difference", "previous_day_difference_sign", "adjusted_yn", "split_rate", "raw_payload"), ("trade_date", "data_source", "market_code", "trading_venue", "collect_cycle", "stock_code")),
    RawTable.FUTURES_QUOTE: RawTableSpec(RawTable.FUTURES_QUOTE, "16_raw_futures_quote.sql", VENUE_SNAPSHOT + ("futures_code", "futures_name", "current_price", "previous_day_difference", "previous_day_difference_sign", "previous_close_price", "change_rate", "open_price", "high_price", "low_price", "upper_limit_price", "lower_limit_price", "base_price", "accumulated_volume", "accumulated_amount", "open_interest", "open_interest_change", "basis", "theoretical_price", "market_basis", "expiration_date", "days_to_expiration", "raw_payload"), ("snapshot_time", "data_source", "market_code", "trading_venue", "collect_cycle", "futures_code")),
    RawTable.FUTURES_MINUTE: RawTableSpec(RawTable.FUTURES_MINUTE, "17_raw_futures_minute.sql", VENUE_BAR + ("futures_code", "open_price", "high_price", "low_price", "close_price", "volume", "accumulated_amount", "raw_payload"), ("bar_time", "data_source", "market_code", "trading_venue", "collect_cycle", "futures_code")),
}


def get_raw_spec(table: RawTable) -> RawTableSpec:
    return RAW_SPECS[table]
