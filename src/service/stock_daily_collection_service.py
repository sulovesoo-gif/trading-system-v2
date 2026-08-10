"""Scheduled official daily RAW collection, independent from minute collection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.service.stock_daily_backfill_service import StockDailyBackfillService, StockDailyBackfillTarget


@dataclass(frozen=True)
class StockDailyCollectionItem:
    stock_code: str
    trading_venue: str
    status: str
    inserted_count: int = 0
    duplicate_count: int = 0
    error: str | None = None


class StockDailyCollectionService:
    """Run one official daily collection pass from common-code targets only."""

    def __init__(self, *, code_repository, calendar, backfill_service: StockDailyBackfillService) -> None:
        self.code_repository = code_repository
        self.calendar = calendar
        self.backfill_service = backfill_service

    def collect_trade_date(self, *, trading_date: date) -> list[StockDailyCollectionItem]:
        targets = self.code_repository.enabled_daily_stocks()
        if trading_date not in self.calendar.open_dates(trading_date, trading_date):
            return [StockDailyCollectionItem(item.stock_code, item.trading_venue, "NON_TRADING_DAY") for item in targets]

        output: list[StockDailyCollectionItem] = []
        for item in targets:
            target = StockDailyBackfillTarget(
                stock_code=item.stock_code,
                market_code=item.market_code,
                trading_venue=item.trading_venue,
                start_date=trading_date,
            )
            try:
                result = self.backfill_service.run_target(target=target, end_date=trading_date)
                output.append(StockDailyCollectionItem(
                    item.stock_code, item.trading_venue, "OK", result.inserted_count, result.duplicate_count
                ))
            except Exception as error:  # one symbol must never stop the remaining targets
                output.append(StockDailyCollectionItem(
                    item.stock_code, item.trading_venue, "FAILED", error=f"{type(error).__name__}: {error}"
                ))
        return output
