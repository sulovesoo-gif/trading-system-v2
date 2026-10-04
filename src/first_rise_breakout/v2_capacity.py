"""V2.0 capacity/Shadow arithmetic. No broker calls or order activation flags."""
from dataclasses import dataclass
from datetime import time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from .strategy import FirstRiseBreakoutStrategy

FORMULA_VERSION = 'FIRST_RISE_J_V2.0_FORMULA_20261004'
CAPACITY_VERSION = 'FIRST_RISE_CAPACITY_V2.0_10PCT'
SHADOW_VERSION = 'FIRST_RISE_SHADOW_FIXED10M_V2.0'


class V2Strategy(FirstRiseBreakoutStrategy):
    STRATEGY_VERSION = FORMULA_VERSION
    USE_PREVIOUS_CLOSE = False
    MIN_PULLBACK = Decimal('.016')
    MAX_PULLBACK = Decimal('.10')
    MIN_PEAK_AGE = timedelta(minutes=6)


def number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('CAPACITY_NON_NUMERIC') from None
    if not result.is_finite():
        raise ValueError('CAPACITY_NON_FINITE')
    return result


@dataclass(frozen=True)
class CapacityConfig:
    participation_pct: Decimal
    shadow_fixed_amount: int
    short_window: int
    long_window: int
    warning_pct: Decimal
    strong_pct: Decimal
    critical_pct: Decimal

    @classmethod
    def from_row(cls, row):
        if row is None or len(row) != 8 or row[0] != 'Y':
            raise ValueError('FIRST_RISE_CAPACITY_CONFIG_MISSING_OR_DISABLED')
        values = [number(v) for v in row[1:]]
        pct, amount, short, long, warning, strong, critical = values
        if not (0 < pct <= 100 and amount == 10000000 and
                0 < short < long and short == int(short) and long == int(long) and
                0 < warning < strong < critical):
            raise ValueError('FIRST_RISE_CAPACITY_CONFIG_INVALID')
        return cls(pct,int(amount),int(short),int(long),warning,strong,critical)

    def evidence(self):
        return dict(capacity_contract=CAPACITY_VERSION,shadow_contract=SHADOW_VERSION,
                    liquidity_participation_pct=str(self.participation_pct),
                    shadow_fixed_amount=self.shadow_fixed_amount,
                    short_window=self.short_window,long_window=self.long_window,
                    warning_pct=str(self.warning_pct),strong_pct=str(self.strong_pct),
                    critical_pct=str(self.critical_pct))


def recent_liquidity(bars, *, signal_time):
    """Five completed rows including the signal bar, with their true predecessor.

    A late first row cannot silently stand in for the session's 09:00 origin.
    Gaps between rows are rejected: cumulative differences spanning missing
    minutes are not one-minute traded amounts.
    """
    rows = sorted((b for b in bars if b.bar_time.date() == signal_time.date()
                   and b.bar_time <= signal_time),key=lambda b:b.bar_time)
    evidence = {'signal_time':signal_time.isoformat(),'recent_5m_traded_amount':None}
    if len(rows) < 5:
        return {**evidence,'reason':'LIQUIDITY_5M_INSUFFICIENT_BARS'}
    selected = rows[-5:]
    evidence['bar_times'] = [b.bar_time.isoformat() for b in selected]
    evidence['accumulated_amounts'] = [str(b.accumulated_amount) for b in selected]
    if selected[-1].bar_time != signal_time or any(
            b.bar_time-a.bar_time != timedelta(minutes=1) for a,b in zip(selected,selected[1:])):
        return {**evidence,'reason':'LIQUIDITY_5M_BAR_GAP'}
    predecessor = rows[-6] if len(rows)>5 else None
    if predecessor is None and selected[0].bar_time.time() != time(9):
        return {**evidence,'reason':'LIQUIDITY_PREDECESSOR_MISSING'}
    if predecessor is not None and selected[0].bar_time-predecessor.bar_time != timedelta(minutes=1):
        return {**evidence,'reason':'LIQUIDITY_PREDECESSOR_GAP'}
    try:
        previous = number(predecessor.accumulated_amount) if predecessor else Decimal(0)
        if previous < 0:raise ValueError('LIQUIDITY_NEGATIVE_AMOUNT')
        amounts = []
        for bar in selected:
            current = number(bar.accumulated_amount)
            if current < 0 or current < previous:raise ValueError('LIQUIDITY_ACCUMULATED_REGRESSION')
            amounts.append(current-previous)
            previous = current
        total = sum(amounts)
        if total <= 0:raise ValueError('LIQUIDITY_ZERO_AMOUNT')
        return {**evidence,'reason':'OK','minute_amounts':[str(v) for v in amounts],
                'recent_5m_traded_amount':str(total)}
    except ValueError as error:
        return {**evidence,'reason':str(error)}


def unbounded_slot(config, realized_net_pnl):
    start, step = number(config.start_slot_amount),number(config.slot_step_amount)
    if start <= 0 or step <= 0:raise ValueError('INVALID_SLOT_RULE')
    pnl = number(realized_net_pnl)
    return start + max(Decimal(0),(pnl/step).to_integral_value(rounding=ROUND_FLOOR))*step


def capacity_sizing(*, config, capacity, realized_net_pnl, broker_cash, reservation,
                    price, buy_fee_rate, liquidity):
    slot = unbounded_slot(config,realized_net_pnl)
    cash, reserved, price, fee = map(number,(broker_cash,reservation,price,buy_fee_rate))
    if cash < 0 or reserved < 0 or price <= 0 or fee < 0:raise ValueError('INVALID_BUY_INPUT')
    evidence = {**capacity.evidence(),**liquidity,'formula_version':FORMULA_VERSION,
        'common_slot_amount':str(slot),'compound_reference':str(number(config.start_slot_amount)+number(realized_net_pnl)),
        'broker_cash':str(cash),'reserved_unacknowledged':str(reserved)}
    if liquidity.get('reason') != 'OK':
        return 0,Decimal(0),evidence
    cap = number(liquidity['recent_5m_traded_amount'])*capacity.participation_pct/100
    target = min(slot,max(Decimal(0),cash-reserved),cap)
    one_share = price*(1+fee)
    qty = int((target/one_share).to_integral_value(rounding=ROUND_FLOOR))
    evidence.update(liquidity_cap_amount=str(cap),target_cash=str(target),requested_quantity=qty,
                    estimated_cash_used=str(qty*one_share),liquidity_cap_hit=(target==cap))
    return qty,qty*one_share,evidence


def shadow_result(*, raw_entry, raw_exit=None, amount=10000000):
    if number(amount) != 10000000:raise ValueError('SHADOW_MUST_BE_FIXED_10M')
    plan=V2Strategy.entry_plan(capital=number(amount),raw_entry_price=number(raw_entry))
    result={'shadow_fixed_amount':int(amount),'shadow_quantity':plan['quantity'],
            'shadow_cash_used':str(plan['cash_used']),'shadow_net_pnl':None,'shadow_net_return':None}
    if raw_exit is not None and plan['quantity']:
        pnl=V2Strategy.one_share_sell_cash(number(raw_exit))*plan['quantity']-plan['cash_used']
        result.update(shadow_net_pnl=str(pnl),shadow_net_return=str(pnl/plan['cash_used']))
    return result


def rolling_comparison(samples, *, count, config):
    if len(samples)<count:return {'level':'NORMAL','status':'INSUFFICIENT_MATCHED_TRADES'}
    rows=samples[-count:]
    shadow=sum(number(s) for s,l in rows)/count
    live=sum(number(l) for s,l in rows)/count
    degradation=(shadow-live)/abs(shadow)*100 if shadow>0 else None
    level='NORMAL'
    if degradation is not None:
        for threshold,name in ((config.warning_pct,'WARNING'),(config.strong_pct,'STRONG_WARNING'),
                               (config.critical_pct,'CRITICAL_REVIEW')):
            if degradation>=threshold:level=name
    return dict(level=level,status='READY',shadow_avg_return=str(shadow),live_avg_return=str(live),
                return_gap_bp=str((live-shadow)*10000),degradation_pct=str(degradation) if degradation is not None else None)
