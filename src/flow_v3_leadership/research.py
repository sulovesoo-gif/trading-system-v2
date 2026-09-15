"""Pure, deterministic Leadership calculations; never writes source records.

Signal math reuses the unmodified FLOW engine. Research costs use the STOCK_LONG
contract: 0.000140527 on each side's notional, plus 0.002 on SELL only.
Capital follows independent PAPER lots, not a shared cash/slot simulation.
"""
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_FLOOR

from src.flow_v3.engine import FlowV3SignalEngine, PAIR_CODE

D = Decimal
STOCK_COST = D('0.000140527')
STOCK_SELL_TAX = D('0.002')
COST_CONTRACT = 'STOCK_LONG fee 0.000140527 each-side notional; sell tax 0.002 sell-side notional'
POLICIES = ('REGULAR', 'EXTENDED_EXIT', 'EXTENDED_FULL', 'AFTER')
PERIODS = ('daily', 'weekly', 'monthly')
VERSION = 'LEADERSHIP_V2_STOCK_SELL_TAX_002'


def stock_costs(quantity, entry_price, exit_price):
    """Exact Decimal research costs; no new rounding/slippage contract."""
    buy_notional = quantity * D(entry_price)
    sell_notional = quantity * D(exit_price)
    gross = sell_notional - buy_notional
    buy_fee = buy_notional * STOCK_COST
    sell_fee = sell_notional * STOCK_COST
    sell_tax = sell_notional * STOCK_SELL_TAX
    return dict(gross=gross, buy_fee=buy_fee, sell_fee=sell_fee, sell_tax=sell_tax,
                net=gross-buy_fee-sell_fee-sell_tax)


@dataclass(frozen=True)
class Session:
    regular_start: time = time(9)
    regular_signal_end: time = time(15, 18)
    regular_execution_end: time = time(15, 19)
    regular_market_end: time = time(15, 30)
    extended_start: time = time(16)  # Requested research eligibility, not a reset.
    extended_end: time = time(20)    # Repository must supply MARKET.INTEGRATED.attr5.

    def allowed(self, at, policy, action, exit_policy='SIGNAL_EOD'):
        if policy not in POLICIES:
            raise ValueError('INVALID_TIME_POLICY')
        regular_end = (self.regular_market_end if action=='EXIT' and
                       (policy!='REGULAR' or exit_policy=='SIGNAL_HOLD') else self.regular_signal_end)
        regular = self.regular_start <= at.time() <= regular_end
        extended = self.extended_start <= at.time() < self.extended_end
        if policy == 'REGULAR' or (policy == 'EXTENDED_EXIT' and action == 'ENTRY'):
            return regular
        return extended if policy == 'AFTER' else regular or extended

    def execution_end(self, policy):
        return self.regular_execution_end if policy == 'REGULAR' else self.extended_end


def build_states(bases):
    """Only observed minutes; missing intervals invalidate windows, never fill them."""
    histories = defaultdict(list)
    engine = FlowV3SignalEngine()
    states = []
    for base in sorted(bases, key=lambda b: (b.bar_time, b.stock_code)):
        key = (base.stock_code, base.business_date)
        history = histories[key]
        state = engine.build_state(base=base, history=history[-30:])
        history.append(state)
        states.append(state)
    return states


def entry_matches(strategy, state, recent):
    """FLOW F1..F4/P0..P2 unchanged; time eligibility is outside this function."""
    if not state.is_complete or strategy.stock_code != state.stock_code:
        return False
    pair = PAIR_CODE[(strategy.entry_fast_period, strategy.entry_slow_period)]
    direction = 1 if strategy.direction == 'LONG' else -1
    family = strategy.entry_family_code
    flow = state.flow_crosses.get(pair) == direction
    if family == 'F1':
        matched = flow
    elif family == 'F2':
        matched = state.velocity_crosses.get(pair) == direction
    elif family == 'F3':
        matched = flow and any(r.business_date == state.business_date and r.is_complete
            and state.bar_time-timedelta(minutes=5) <= r.bar_time < state.bar_time
            and r.velocity_crosses.get(pair) == direction for r in recent)
    elif family == 'F4':
        matched = flow and (state.long_absorption if direction == 1 else state.short_absorption)
    else:
        raise ValueError('UNKNOWN_ENTRY_FAMILY')
    return matched and FlowV3SignalEngine._program_condition(strategy, state, direction)


class Prices:
    def __init__(self, rows):
        self.values = {}
        for at, price in rows:
            if price is None or D(price) <= 0:
                continue
            if at in self.values and self.values[at] != D(price):
                raise ValueError('AMBIGUOUS_PRICE_SOURCE')
            self.values[at] = D(price)
        self.times = sorted(self.values)

    def next(self, signal, end, same_day=False, eligible=None):
        i = bisect_right(self.times, signal)
        while i < len(self.times) and eligible is not None and not eligible(self.times[i]):
            i += 1
        if i == len(self.times):
            return None
        at = self.times[i]
        if at > end or (same_day and at.date() != signal.date()):
            return None
        return at, self.values[at]

    def eod(self, day, cutoff):
        at = datetime.combine(day, cutoff)
        i = bisect_right(self.times, at)-1
        if i < 0 or self.times[i].date() != day:
            return None
        at = self.times[i]
        return at, self.values[at]


def extended_trades(strategy, states, prices, policy, session, asof):
    """Independent lots; first reverse exit per lot, no occupancy ENTRY gate."""
    if strategy.direction != 'LONG':
        raise ValueError('UNDERLYING_LONG_ONLY')
    pair = PAIR_CODE[(strategy.exit_fast_period, strategy.exit_slow_period)]
    source = 'velocity_crosses' if strategy.entry_family_code == 'F2' else 'flow_crosses'
    def price_eligible(t):
        if policy == 'AFTER': return session.extended_start <= t.time() <= session.extended_end
        return (session.regular_start <= t.time() <= session.regular_market_end
                or session.extended_start <= t.time() <= session.extended_end)
    def entry_price_eligible(t):
        if policy=='EXTENDED_EXIT':
            cutoff=session.regular_execution_end if strategy.exit_policy_code=='SIGNAL_EOD' else session.regular_market_end
            return session.regular_start <= t.time() <= cutoff
        return price_eligible(t)
    trades, opened, recent, issues = [], [], [], set()
    by_day = defaultdict(list)
    for state in states:
        if state.stock_code == strategy.stock_code and state.business_date <= asof:
            by_day[state.business_date].append(state)
    for day, daily in sorted(by_day.items()):
        recent = []  # New trading date, not 16:00.
        day_end = datetime.combine(day, session.execution_end(policy))
        for state in sorted(daily, key=lambda x: x.bar_time):
            if session.allowed(state.bar_time, policy, 'EXIT',strategy.exit_policy_code) and state.is_complete:
                if getattr(state, source).get(pair) == -1:
                    for t in opened[:]:
                        if t['entry_signal_time'] >= state.bar_time:
                            continue
                        end = day_end if strategy.exit_policy_code == 'SIGNAL_EOD' else datetime.combine(asof, session.extended_end)
                        price = prices.next(state.bar_time, end, strategy.exit_policy_code == 'SIGNAL_EOD', price_eligible)
                        if price and price[0] >= t['entry_execution_time']:
                            t.update(actual_exit_time=price[0], actual_exit_price=price[1],
                                     normal_exit_signal_time=state.bar_time, exit_reason='NORMAL_EXIT')
                            opened.remove(t)
                        else:
                            issues.add('MISSING_EXIT_PRICE')
            if session.allowed(state.bar_time, policy, 'ENTRY') and entry_matches(strategy,state,recent):
                price = prices.next(state.bar_time, day_end, True, entry_price_eligible)
                if not price:
                    issues.add('MISSING_ENTRY_PRICE')
                else:
                    t = dict(paper_trade_id=len(trades)+1, entry_signal_time=state.bar_time,
                        entry_execution_time=price[0], entry_execution_price=price[1],
                        actual_exit_time=None, actual_exit_price=None, exit_reason=None)
                    trades.append(t); opened.append(t)
            recent.append(state)
            recent = recent[-6:]
        if strategy.exit_policy_code == 'SIGNAL_EOD':
            for t in opened[:]:
                price = prices.eod(day, session.execution_end(policy))
                if price and price[0] >= t['entry_execution_time']:
                    t.update(actual_exit_time=price[0], actual_exit_price=price[1], exit_reason='SIGNAL_EOD')
                    opened.remove(t)
                else:
                    issues.add('MISSING_EOD_PRICE')
    return trades, sorted(issues)


def replay(trades, capital_base, asof):
    """Replay once per capital; period boundaries retain HOLD lots and sizing.

    Daily/WTD/MTD use realized capital at the period boundary, including PnL
    from earlier OPEN entries when they close. MDD is realized-capital MDD.
    A earlier EXIT, B all ENTRY, C same-lot same-time EXIT (stable ID ties).
    """
    base = D(capital_base)
    if not base.is_finite() or base <= 0:
        raise ValueError('INVALID_CAPITAL')
    starts = {'daily':asof, 'weekly':asof-timedelta(days=asof.weekday()),
              'monthly':asof.replace(day=1)}
    metrics = {p:dict(status='COMPLETE', initial_capital=base, final_capital=base,
        net_profit=D(0), compound_return=D(0), trade_count=0, win_count=0,
        win_rate=None, mdd=D(0), rank=None, normal_exit_count=0,eod_exit_count=0,
        overnight_count=0, maximum_quantity=0, skipped_quantity_count=0) for p in PERIODS}
    peaks, initialized = {}, set()
    events, seen = [], set()
    for t in trades:
        tid = t['paper_trade_id']
        if tid in seen: raise ValueError('DUPLICATE_TRADE')
        seen.add(tid)
        entry, end = t['entry_execution_time'],t.get('actual_exit_time')
        if end is not None and end < entry: raise ValueError('INVALID_EXIT_TIME')
        if t.get('normal_exit_signal_time') and t['normal_exit_signal_time'] < t['entry_signal_time']:
            raise ValueError('INVALID_SIGNAL_ORDER')
        events.append((entry,1,tid,t))
        if end: events.append((end,2 if end==entry else 0,tid,t))
    capital, lots = base, {}
    for at,kind,tid,t in sorted(events,key=lambda e:e[:3]):
        if at.date() > asof: break
        periods = [p for p in PERIODS if at.date() >= starts[p]]
        for p in periods:
            if p not in initialized:
                metrics[p]['initial_capital']=capital; peaks[p]=capital; initialized.add(p)
        if kind == 1:
            price = D(t['entry_execution_price'])
            if not price.is_finite() or price <= 0: raise ValueError('INVALID_PRICE')
            qty = max(0,int((capital/price).to_integral_value(rounding=ROUND_FLOOR)))
            lots[tid]=(qty,price)
            for p in periods:
                metrics[p]['maximum_quantity']=max(metrics[p]['maximum_quantity'],qty)
                metrics[p]['skipped_quantity_count'] += qty == 0
        else:
            qty,entry = lots.pop(tid)
            exit_price = D(t['actual_exit_price'])
            if not exit_price.is_finite() or exit_price<=0: raise ValueError('INVALID_PRICE')
            net = stock_costs(qty, entry, exit_price)['net']
            capital += net
            for p in periods:
                m=metrics[p]
                m['trade_count'] += qty > 0; m['win_count'] += qty>0 and net>0
                m['normal_exit_count'] += qty>0 and t.get('exit_reason')!='SIGNAL_EOD'
                m['eod_exit_count'] += qty>0 and t.get('exit_reason')=='SIGNAL_EOD'
                m['overnight_count'] += qty>0 and at.date()>t['entry_execution_time'].date()
                m['maximum_quantity']=max(m['maximum_quantity'],qty)
                peaks[p]=max(peaks[p],capital)
                if peaks[p]>0: m['mdd']=max(m['mdd'],100*(1-capital/peaks[p]))
    for p,m in metrics.items():
        if p not in initialized: m['initial_capital']=capital
        m['final_capital']=capital
        m['net_profit']=capital-m['initial_capital']
        m['compound_return']=100*m['net_profit']/m['initial_capital'] if m['initial_capital']>0 else None
        m['win_rate']=D(100)*m['win_count']/m['trade_count'] if m['trade_count'] else None
        m['open_count']=sum(q>0 for q,_ in lots.values())
    return metrics


def unavailable(reason):
    return dict(status=reason,initial_capital=None,final_capital=None,net_profit=None,
        compound_return=None,trade_count=None,win_count=None,win_rate=None,mdd=None,rank=None)


def rank_rows(rows):
    """Global LONG universe ordinal: return DESC, net DESC, strategy_id ASC."""
    for capital in {r['capital_base'] for r in rows}:
        for policy in POLICIES:
            for period in PERIODS:
                key=policy.lower()+'_'+period
                eligible=[r for r in rows if r['capital_base']==capital and r[key]['status']=='COMPLETE'
                          and r[key]['compound_return'] is not None]
                eligible.sort(key=lambda r:(-r[key]['compound_return'],-r[key]['net_profit'],r['strategy_id']))
                for rank,row in enumerate(eligible,1): row[key]['rank']=rank
    return rows
