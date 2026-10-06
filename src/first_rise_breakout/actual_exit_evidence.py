"""Capacity evidence only; never supplies prices or decisions to order planning."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from .v2_capacity import recent_liquidity


def actual_exit_evidence(*, observed_at, reason, quantity, amount, bars):
    liquidity={'reason':'ACTUAL_EXIT_OBSERVATION_UNAVAILABLE','recent_5m_traded_amount':None}
    if observed_at is not None:
        # At 14:10:04, the latest completed bar is 14:09, never 14:10/14:14.
        latest=observed_at.replace(second=0,microsecond=0)-timedelta(minutes=1)
        liquidity=recent_liquidity(bars,signal_time=latest)
    total=liquidity.get('recent_5m_traded_amount')
    return dict(actual_exit_observed_at=observed_at.isoformat() if observed_at else None,
        actual_exit_reason=reason,actual_sell_quantity=int(quantity),actual_sell_amount=str(amount),
        actual_sell_average_price=str(Decimal(amount)/quantity) if quantity else None,
        actual_exit_recent_5m_amount=total,
        actual_exit_participation_pct=str(Decimal(amount)/Decimal(total)*100) if total and quantity else None,
        actual_exit_liquidity=liquidity,
        actual_exit_time_basis='LAST_OWNED_POSITIVE_SELL_CHECKPOINT_OBSERVATION',
        actual_exit_liquidity_basis='COMPLETED_RAW_AVAILABLE_BY_OBSERVATION')


def load_actual_exit(q,*,trade,stock,quantity,amount):
    q.execute('''SELECT a.fill_observed_at,r.detail->>'actual_exit_reason'
        FROM first_rise_j_live_checkpoint_allocation a
        JOIN first_rise_j_live_cost c USING(cost_trade_id)
        JOIN live_broker_order o USING(broker_order_id)
        JOIN live_order_request r USING(order_request_id)
        WHERE c.trade_id=%s AND a.side='SELL'
        ORDER BY a.fill_observed_at DESC NULLS LAST,a.checkpoint_version DESC LIMIT 1''',(trade,))
    row=q.fetchone();observed,reason=row if row else (None,None)
    bars=[]
    if observed is not None:
        q.execute('''SELECT bar_time,accumulated_amount FROM first_rise_completed_minute_raw
            WHERE business_date=%s AND stock_code=%s AND source='KIS_FHKST03010200'
              AND bar_time<%s AND observed_as_of<=%s ORDER BY bar_time DESC LIMIT 6''',
            (observed.date(),stock,observed.replace(second=0,microsecond=0),observed))
        bars=[SimpleNamespace(bar_time=t,accumulated_amount=a) for t,a in q.fetchall()]
    return actual_exit_evidence(observed_at=observed,reason=reason,quantity=quantity,amount=amount,bars=bars)
