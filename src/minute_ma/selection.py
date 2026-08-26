from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from .contracts import Axis


def _first(row: Mapping[str,str], *names: str) -> str | None:
    normalized = {str(k).strip().lower(): str(v).strip() for k,v in row.items() if k is not None and v is not None}
    for name in names:
        value = normalized.get(name.lower())
        if value not in (None,""):
            return value
    return None


def _decimal(row: Mapping[str,str], *names: str) -> Decimal | None:
    value = _first(row,*names)
    return None if value is None else Decimal(value.replace(",",""))


def _integer(row: Mapping[str,str], *names: str) -> int | None:
    value = _decimal(row,*names)
    return None if value is None else int(value)


@dataclass(frozen=True)
class HistoricalMetric:
    source_daily_strategy_id: str
    compound_return_pct: Decimal
    completed_trade_count: int | None
    win_rate_pct: Decimal | None
    avg_net_return_pct: Decimal | None
    median_net_return_pct: Decimal | None
    compound_profit: Decimal | None
    final_compound_capital: Decimal | None
    max_concurrent_open: int | None
    avg_hold_minutes: Decimal | None
    worst_trade_pct: Decimal | None
    mdd_pct: Decimal | None
    source_row: Mapping[str,str]


def load_historical_csv(path: Path) -> dict[str,HistoricalMetric]:
    with path.open("r",encoding="utf-8-sig",newline="") as handle:
        rows = list(csv.DictReader(handle))
    metrics: dict[str,HistoricalMetric] = {}
    for row in rows:
        strategy_id = _first(row,"source_daily_strategy_id","strategy_id","전략id","전략_id")
        compound = _decimal(row,"compound_return_pct","누적복리수익률_pct","복리수익률_pct","compound_return")
        if strategy_id is None or compound is None:
            raise ValueError(f"missing strategy identity/compound return in {path.name}: {row}")
        if strategy_id in metrics:
            raise ValueError(f"duplicate strategy {strategy_id} in {path.name}")
        metrics[strategy_id]=HistoricalMetric(
            strategy_id,compound,
            _integer(row,"trade_count","completed_trade_count","거래수","완료거래수"),
            _decimal(row,"win_rate_pct","win_rate","승률","승률_pct"),
            _decimal(row,"avg_net_return_pct","평균순수익률_pct"),
            _decimal(row,"median_net_return_pct","중앙순수익률_pct"),
            _decimal(row,"compound_profit","누적복리손익","누적복리수익금","복리수익금"),
            _decimal(row,"final_compound_capital","최종복리자본"),
            _integer(row,"max_concurrent_open","최대동시open"),
            _decimal(row,"avg_hold_minutes","평균보유시간","평균보유분"),
            _decimal(row,"worst_trade_pct","최대손실_pct","최악거래_pct"),
            _decimal(row,"mdd_pct","mdd","mdd_pct"),row,
        )
    if len(metrics)!=2400:
        raise ValueError(f"{path.name} must contain exactly 2400 strategy rows; got {len(metrics)}")
    return metrics


AVAILABLE_AXES=(Axis.KRX_CONTINUOUS,Axis.KRX_RESET,Axis.INTEGRATED_CONTINUOUS)


def build_selection_rows(files: Mapping[Axis,Path]) -> dict[Axis,dict[str,HistoricalMetric]]:
    if set(files)!=set(AVAILABLE_AXES):
        raise ValueError("exactly the three approved historical axes are required")
    loaded={axis:load_historical_csv(path) for axis,path in files.items()}
    identities=[set(rows) for rows in loaded.values()]
    if identities[1:] != identities[:-1]:
        raise ValueError("the three historical artifacts do not contain the same 2400 semantics")
    return loaded
