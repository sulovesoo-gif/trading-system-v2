"""Read-only target-capital projection of persisted research cycles.

This module intentionally does not create or modify replay rows.  It lets the
research dashboard compare instruments with different target capital using the
same exact entry/exit prices already persisted by a completed run.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Mapping

STOCK_TARGET_CAPITAL = Decimal("10000000")
ETF_TARGET_CAPITAL = Decimal("1000000")
TARGET_CAPITAL_BY_STOCK = {"000660": STOCK_TARGET_CAPITAL, "0193T0": ETF_TARGET_CAPITAL, "0197X0": ETF_TARGET_CAPITAL}


def target_capital(stock_code: str) -> Decimal:
    """Official research scale: common share 10m KRW, ETF/ETN 1m KRW."""
    return TARGET_CAPITAL_BY_STOCK.get(stock_code, ETF_TARGET_CAPITAL)


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def project_cycle(cycle: Mapping, *, fee_rate: Decimal, sell_tax_rate: Decimal) -> dict:
    """Recalculate one stored closed cycle at its instrument target capital.

    Accumulated legs retain their persisted ratios and entry prices.  The
    projection never substitutes prices and has no database side effect.
    """
    capital = target_capital(str(cycle["trade_stock_code"]))
    direction = str(cycle["direction"])
    exit_price = Decimal(str(cycle["exit_price"]))
    legs = cycle.get("legs") or [{"entry_price": cycle["entry_price"], "entry_ratio": Decimal("1")}]
    normalized = []
    for leg in legs:
        ratio = Decimal(str(leg.get("entry_ratio") or 1))
        entry_price = Decimal(str(leg["entry_price"]))
        quantity = int((capital * ratio) // entry_price)
        invested = entry_price * quantity
        gross = (exit_price - entry_price if direction == "LONG" else entry_price - exit_price) * quantity
        normalized.append({"entry_price": entry_price, "entry_ratio": ratio, "quantity": quantity, "invested_amount": invested, "gross": gross})
    quantity = sum(item["quantity"] for item in normalized)
    invested = sum((item["invested_amount"] for item in normalized), Decimal("0"))
    gross = _money(sum((item["gross"] for item in normalized), Decimal("0")))
    buy_fee = _money(invested * fee_rate)
    sell_notional = exit_price * quantity
    sell_fee = _money(sell_notional * fee_rate)
    sell_tax = _money(sell_notional * sell_tax_rate)
    total_cost = buy_fee + sell_fee + sell_tax
    net = gross - total_cost
    result = dict(cycle)
    result.update(target_capital=capital, quantity=quantity, invested_amount=invested,
                  gross_realized_profit=gross, buy_fee=buy_fee, sell_fee=sell_fee,
                  sell_tax=sell_tax, total_trading_cost=total_cost, realized_profit=net,
                  gross_invested_return_rate=Decimal("0") if not invested else gross / invested * 100,
                  invested_return_rate=Decimal("0") if not invested else net / invested * 100,
                  gross_capital_return_rate=gross / capital * 100,
                  capital_return_rate=net / capital * 100)
    return result


def aggregate(cycles: Iterable[Mapping]) -> dict:
    rows = list(cycles)
    total = lambda key: sum((Decimal(str(row.get(key) or 0)) for row in rows), Decimal("0"))
    closed = len(rows); gross = total("gross_realized_profit"); net = total("realized_profit"); invested = total("invested_amount")
    wins = sum(Decimal(str(row["realized_profit"])) > 0 for row in rows)
    losses = sum(Decimal(str(row["realized_profit"])) < 0 for row in rows)
    flats = closed - wins - losses
    targets = total("target_capital")
    return {"closed_count": closed, "win_count": wins, "loss_count": losses, "flat_count": flats,
            "win_rate": Decimal("0") if not closed else Decimal(wins) / closed * 100,
            "gross_realized_profit": gross, "total_trading_cost": total("total_trading_cost"), "realized_profit": net,
            "invested_amount": invested, "target_capital": targets,
            "gross_invested_return_rate": Decimal("0") if not invested else gross / invested * 100,
            "invested_return_rate": Decimal("0") if not invested else net / invested * 100,
            "gross_capital_return_rate": Decimal("0") if not targets else gross / targets * 100,
            "capital_return_rate": Decimal("0") if not targets else net / targets * 100,
            "avg_trade_return_rate": Decimal("0") if not closed else sum((Decimal(str(row["invested_return_rate"])) for row in rows), Decimal("0")) / closed,
            "avg_holding_seconds": Decimal("0") if not closed else sum((Decimal(str(row.get("holding_seconds") or 0)) for row in rows), Decimal("0")) / closed,
            "signal_exit_profit": sum((Decimal(str(row["realized_profit"])) for row in rows if row.get("exit_type") == "SIGNAL"), Decimal("0")),
            "session_close_profit": sum((Decimal(str(row["realized_profit"])) for row in rows if row.get("exit_type") == "SESSION_CLOSE"), Decimal("0"))}
