"""Deterministic PAPER-only accounting; no transport, broker, or account gate.

Research cost contract: 07_Daily_Incremental_PAPER_Update V0.1, section J.
Capital is independent per strategy, not a simulated shared cash account.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from statistics import median

D = Decimal
FEE = D('1.46527') / D('10000')
SLIPPAGE = D('2') / D('10000')
ROUND_TRIP = (FEE + SLIPPAGE) * 2
VERSION = 'PAPER_INDEPENDENT_V1'


class AccountingError(ValueError):
    pass


def positive_price(value):
    if value is None:
        raise AccountingError('MISSING_PRICE')
    value = D(str(value))
    if not value.is_finite() or value <= 0:
        raise AccountingError('INVALID_PRICE')
    return value


def quantity(capital, price):
    """Variable integer shares; never clamp to one share or borrow capital."""
    capital = D(str(capital))
    price = positive_price(price)
    if not capital.is_finite():
        raise AccountingError('INVALID_CAPITAL')
    return max(0, int((capital / price).to_integral_value(rounding=ROUND_FLOOR)))


def calculate(trades, *, previous_close):
    """Replay bounded strategy history. EXIT precedes ENTRY at equal timestamps.

    All input lots remain observed, including lots sized at zero. Realized net
    is added once; OPEN profits never enter capital. Rebuild replaces projections
    rather than incrementing stored balances, so late/corrected input converges.
    """
    initial = positive_price(previous_close) * D('1.5')
    capital = peak = initial
    drawdown = D(0)
    lots, events = {}, []
    seen = set()
    for t in trades:
        tid = t['paper_trade_id']
        if tid in seen:
            raise AccountingError('DUPLICATE_TRADE_ID')
        seen.add(tid)
        if t['trade_status'] == 'CANCELLED':
            continue
        entry = t['entry_execution_time']
        if entry is None:
            raise AccountingError('MISSING_ENTRY_TIME')
        positive_price(t['entry_execution_price'])
        events.append((entry, 1, tid, t))
        if t['trade_status'] == 'CLOSED':
            end = t['actual_exit_time']
            if end is None or end <= entry:
                raise AccountingError('INVALID_EXIT_TIME')
            positive_price(t['actual_exit_price'])
            events.append((end, 0, tid, t))
    daily = defaultdict(lambda: dict(entry_count=0, closed_count=0, open_count=0,
                                     win_count=0, loss_count=0, net_pnl=D(0), returns=[]))
    max_qty = opened = max_open = 0
    for at, kind, tid, t in sorted(events, key=lambda e: e[:3]):
        day = daily[at.date()]
        day.setdefault('starting_capital', capital)
        if kind == 1:
            price = positive_price(t['entry_execution_price'])
            qty = quantity(capital, price)
            max_qty = max(max_qty, qty)
            opened += 1
            max_open = max(max_open, opened)
            lots[tid] = dict(paper_trade_id=tid, quantity=qty, capital_at_entry=capital,
                            gross_return_pct=None, net_return_pct=None, gross_pnl=None,
                            buy_fee=None, sell_fee=None, slippage=None, net_pnl=None,
                            reason='INSUFFICIENT_RESEARCH_CAPITAL' if qty == 0 else None,
                            entry_time=at, exit_time=None)
            day['entry_count'] += 1
        else:
            lot = lots[tid]
            entry = positive_price(t['entry_execution_price'])
            exit_price = positive_price(t['actual_exit_price'])
            notional = entry * lot['quantity']
            gross_pct = (exit_price / entry - 1) * 100
            net_pct = gross_pct - ROUND_TRIP * 100
            # Exact research return deduction uses ENTRY notional for both sides.
            # These are research costs, not estimates of a broker settlement.
            net = notional * net_pct / 100
            lot.update(gross_return_pct=gross_pct, net_return_pct=net_pct,
                       gross_pnl=(exit_price-entry)*lot['quantity'],
                       buy_fee=notional*FEE, sell_fee=notional*FEE,
                       slippage=notional*SLIPPAGE*2, net_pnl=net, exit_time=at)
            capital += net
            peak = max(peak, capital)
            drawdown = min(drawdown, (capital/peak-1)*100)
            opened -= 1
            day['closed_count'] += 1
            day['win_count'] += net_pct > 0
            day['loss_count'] += net_pct < 0
            day['returns'].append(net_pct)
            day['net_pnl'] += net
        day['ending_capital'] = capital
        day['open_count'] = opened
    for day in daily.values():
        returns = day.pop('returns')
        day['avg_net_return_pct'] = sum(returns)/len(returns) if returns else None
        day['median_net_return_pct'] = median(returns) if returns else None
        day['win_rate_pct'] = D(day['win_count'])*100/len(returns) if returns else None
        day['capital_return_pct'] = ((day['ending_capital']/day['starting_capital']-1)*100
                                     if day['starting_capital'] > 0 else None)
    closed = [x for x in lots.values() if x['exit_time'] is not None]
    returns = [x['net_return_pct'] for x in closed]
    summary = dict(initial_capital=initial, current_capital=capital,
                   net_pnl=capital-initial, compound_return_pct=(capital/initial-1)*100,
                   max_drawdown_pct=drawdown, maximum_quantity=max_qty,
                   trade_count=len(lots), closed_count=len(closed), open_count=opened,
                   max_concurrent_open=max_open, win_count=sum(x>0 for x in returns),
                   loss_count=sum(x<0 for x in returns),
                   avg_net_return_pct=sum(returns)/len(returns) if returns else None,
                   median_net_return_pct=median(returns) if returns else None,
                   win_rate_pct=D(sum(x>0 for x in returns))*100/len(returns) if returns else None)
    return lots, daily, summary
