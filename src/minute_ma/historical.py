from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal,ROUND_HALF_UP
from statistics import median
from typing import Iterable, Mapping

from .contracts import MinuteBar, MinuteMaPath
from .engine import MinuteMaSignalEngine, SignalType


ROUND_TRIP_COST_PCT = Decimal("0.20")
INITIAL_CAPITAL = Decimal("1000000")


@dataclass(frozen=True)
class HistoricalTrade:
    entry_execution_time: datetime
    exit_execution_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    exit_reason: str
    basis_capital: Decimal
    net_return_pct: Decimal
    realized_pnl: Decimal

    @property
    def hold_minutes(self) -> Decimal:
        seconds = Decimal(str((self.exit_execution_time-self.entry_execution_time).total_seconds()))
        return seconds/Decimal("60")


@dataclass(frozen=True)
class HistoricalResult:
    source_daily_strategy_id: str
    path: MinuteMaPath
    trades: tuple[HistoricalTrade, ...]
    max_concurrent_open: int
    final_compound_capital: Decimal
    mdd_pct: Decimal | None

    @property
    def compound_return_pct(self) -> Decimal:
        return (self.final_compound_capital/INITIAL_CAPITAL-Decimal("1"))*Decimal("100")


class MinuteMaHistoricalReplay:
    """Deterministic PAPER-contract replay over an already prepared MA stream."""

    def __init__(self, *, engine: MinuteMaSignalEngine | None = None) -> None:
        self.engine = engine or MinuteMaSignalEngine()

    def replay(
        self,
        *,
        source_daily_strategy_id: str,
        path: MinuteMaPath,
        prepared_points,
        execution_bars: Mapping[datetime, MinuteBar],
        evaluation_from: date,
        evaluation_to: date,
    ) -> HistoricalResult:
        events = [
            event for event in self.engine.evaluate_prepared(path=path, points=prepared_points)
            if evaluation_from <= event.source_bar_time.date() <= evaluation_to
        ]
        events.sort(key=lambda event: (
            event.source_bar_time,
            0 if event.signal_type is SignalType.EXIT else 1,
            event.signal_event_key,
        ))
        by_date: dict[date,list] = {}
        for event in events:
            by_date.setdefault(event.source_bar_time.date(),[]).append(event)

        current_capital=INITIAL_CAPITAL
        peak_capital=INITIAL_CAPITAL
        worst_drawdown=Decimal("0")
        max_concurrent=0
        completed: list[HistoricalTrade] = []

        for trading_date in sorted(by_date):
            open_trades: list[tuple[datetime,Decimal,Decimal]] = []

            def close_all(*, at: datetime, price: Decimal, reason: str) -> None:
                nonlocal current_capital,peak_capital,worst_drawdown
                pending=list(open_trades)
                open_trades.clear()
                for entry_time,entry_price,basis_capital in pending:
                    gross=(price/entry_price-Decimal("1"))*Decimal("100")
                    net=gross-ROUND_TRIP_COST_PCT
                    pnl=basis_capital*net/Decimal("100")
                    current_capital+=pnl
                    peak_capital=max(peak_capital,current_capital)
                    drawdown=(current_capital/peak_capital-Decimal("1"))*Decimal("100")
                    worst_drawdown=min(worst_drawdown,drawdown)
                    completed.append(HistoricalTrade(
                        entry_time,at,entry_price,price,reason,basis_capital,net,pnl,
                    ))

            for event in by_date[trading_date]:
                if (event.signal_type is SignalType.ENTRY
                        and not path.axis.allows_entry_source_time(event.source_bar_time.time())):
                    continue
                proxy_time=event.source_bar_time+timedelta(minutes=1)
                if not time(9,0) <= proxy_time.time() <= time(15,19):
                    continue
                proxy=execution_bars.get(proxy_time)
                if proxy is None:
                    continue
                price=Decimal(str(proxy.open_price))
                if event.signal_type is SignalType.EXIT:
                    close_all(at=proxy_time,price=price,reason="NORMAL_EXIT")
                else:
                    open_trades.append((proxy_time,price,current_capital))
                    max_concurrent=max(max_concurrent,len(open_trades))

            eod_time=datetime.combine(trading_date,time(15,19))
            eod_bar=execution_bars.get(eod_time)
            if open_trades and eod_bar is not None:
                close_all(
                    at=eod_time,
                    price=Decimal(str(eod_bar.open_price)),
                    reason="EOD_1519",
                )

        return HistoricalResult(
            source_daily_strategy_id,
            path,
            tuple(completed),
            max_concurrent,
            current_capital,
            worst_drawdown if completed else None,
        )


def result_row(result: HistoricalResult) -> dict[str, object]:
    trades=result.trades
    returns=[trade.net_return_pct for trade in trades]
    count=len(trades)
    q=lambda value,places: (None if value is None else
                            value.quantize(Decimal(places),rounding=ROUND_HALF_UP))
    return {
        "계산방식":result.path.axis.value,
        "전략id":result.source_daily_strategy_id,
        "신호종목":result.path.signal_code,
        "실행상품코드":result.path.execution_code,
        "방향":result.path.direction,
        "진입ma":f"{result.path.entry_fast_ma}/{result.path.entry_slow_ma}",
        "청산ma":f"{result.path.exit_fast_ma}/{result.path.exit_slow_ma}",
        "추세ma":"NONE" if result.path.trend_ma is None else result.path.trend_ma,
        "거래수":count,
        "정상청산수":sum(trade.exit_reason == "NORMAL_EXIT" for trade in trades),
        "마감1519청산수":sum(trade.exit_reason == "EOD_1519" for trade in trades),
        "최대동시open":result.max_concurrent_open,
        "승률_pct":q(Decimal("100")*sum(value>0 for value in returns)/count if count else None,"0.01"),
        "평균순수익률_pct":q(sum(returns,Decimal("0"))/count if count else None,"0.0001"),
        "중앙순수익률_pct":q(Decimal(str(median(returns))) if count else None,"0.0001"),
        "평균보유분":q(sum((trade.hold_minutes for trade in trades),Decimal("0"))/count if count else None,"0.01"),
        "최악거래_pct":q(min(returns) if count else None,"0.0001"),
        "최고거래_pct":q(max(returns) if count else None,"0.0001"),
        "누적복리손익":q(result.final_compound_capital-INITIAL_CAPITAL,"1"),
        "최종복리자본":q(result.final_compound_capital,"1"),
        "누적복리수익률_pct":q(result.compound_return_pct,"0.0001"),
        "mdd_pct":q(result.mdd_pct,"0.0001"),
    }


HISTORICAL_COLUMNS=(
    "계산방식", "전략id", "신호종목", "실행상품코드", "방향",
    "진입ma", "청산ma", "추세ma", "거래수", "정상청산수",
    "마감1519청산수", "최대동시open", "승률_pct", "평균순수익률_pct",
    "중앙순수익률_pct", "평균보유분", "최악거래_pct", "최고거래_pct",
    "누적복리손익", "최종복리자본", "누적복리수익률_pct", "mdd_pct",
)
