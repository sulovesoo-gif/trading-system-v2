"""V1.8 actual-fill provisional compounding and final-cost delta settlement.

Caller supplies a transaction cursor. Checkpoint, CLOSE, cost row, and epoch
event commit together; this module never submits orders or invents fills.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
import logging

from psycopg.types.json import Jsonb

from .j_capital import finite_decimal
from .j_epoch import JCapitalEpochRepository


@dataclass(frozen=True)
class ProvisionalRates:
    # V1.8 section 5 reference rates, explicitly provisional, not actual fees.
    buy_fee: Decimal = Decimal('0.000146527')
    sell_fee: Decimal = Decimal('0.000146527')
    sell_tax: Decimal = Decimal('0.002')
    other_cost: Decimal = Decimal('0')

    def evidence(self):
        values = {name: finite_decimal(getattr(self, name), name)
                  for name in ('buy_fee', 'sell_fee', 'sell_tax', 'other_cost')}
        if any(value < 0 for value in values.values()):
            raise ValueError('FIRST_RISE_NEGATIVE_COST_RATE')
        return {'contract': 'FIRST_RISE_LIVE_COST_V1.8',
                'basis': 'actual_fill_amount', 'rounding': 'exact_decimal_provisional',
                **{name: str(value) for name, value in values.items()}}


def provisional_costs(buy_amount, sell_amount, rates=ProvisionalRates()):
    rates.evidence()
    buy = finite_decimal(buy_amount, 'buy_amount')
    sell = finite_decimal(sell_amount, 'sell_amount')
    if buy <= 0 or sell <= 0:
        raise ValueError('FIRST_RISE_ACTUAL_FILL_AMOUNT_REQUIRED')
    # No synthetic slippage, no price rounding, no assertion of broker fee rounding.
    return (buy*rates.buy_fee, sell*rates.sell_fee, sell*rates.sell_tax, rates.other_cost)


def _lock(q):
    q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")


def retain_exit_recovery(q,*,request_id,broker_id,at,source):
    """Durable rejection evidence; never an enable/retry flag or fabricated code."""
    q.execute('''SELECT r.execution_stock_code FROM live_order_request r WHERE r.order_request_id=%s
        AND r.strategy_instance_id='FIRST_RISE_J_V1.3' AND r.side='SELL' ''',(request_id,))
    row=q.fetchone()
    if row is None:return
    q.execute('''SELECT COALESCE(sum(c.buy_quantity-c.sell_quantity),0) FROM first_rise_j_live_cost c
        JOIN first_rise_j_capital_binding b ON b.trade_id=c.trade_id WHERE b.stock_code=%s''',(row[0],))
    owned=q.fetchone()[0]
    q.execute('''SELECT detail FROM live_broker_order_audit WHERE broker_order_id=%s
        AND event_type='FIRST_RISE_POST_RESPONSE' ORDER BY audit_id DESC LIMIT 1''',(broker_id,))
    response=q.fetchone()
    detail=response[0] if response else {}
    evidence={'exit_state':'EXIT_RECOVERY','remaining_owned_quantity':int(owned),
        'rejection_evidence':{key:detail.get(key) for key in ('rt_cd','msg_cd','msg1')},
        'recovery_observed_at':at.isoformat(),'recovery_source':source}
    q.execute('''UPDATE live_order_request SET reason='FIRST_RISE_EXIT_RECOVERY_REJECTED',detail=detail || %s
        WHERE order_request_id=%s''',(Jsonb(evidence),request_id))
    logging.getLogger(__name__).warning('FIRST_RISE_EXIT_RECOVERY_REJECTED broker_order_id=%s stock_code=%s remaining_owned_qty=%s',
        broker_id,row[0],owned)


def _settle(q, *, trade_id, key, amount, at, evidence):
    class SameTransaction:
        @contextmanager
        def connection(self):
            yield q.connection
    return JCapitalEpochRepository(SameTransaction()).settle_actual(
        event_key=key, trade_id=trade_id, net_pnl_delta=amount,
        settled_at=at, evidence=evidence)


def record_checkpoint(q, *, trade_id, broker_order_id, version, quantity, amount, at):
    """Persist an ADVANCED cumulative-checkpoint delta, not a synthetic broker fill.

    The durable submit request must carry first_rise_trade_id in its detail.
    Other strategies' orders cannot be attributed to FIRST_RISE by the caller.
    """
    _lock(q)
    amount = finite_decimal(amount, 'checkpoint_amount')
    if quantity <= 0 or int(quantity) != quantity or version <= 0 or amount <= 0:
        raise ValueError('FIRST_RISE_INVALID_CHECKPOINT')
    q.execute('''SELECT b.epoch_id,b.stock_code,o.side,o.quantity
        FROM first_rise_j_capital_binding b
        JOIN live_order_request r ON r.detail->>'first_rise_trade_id'=b.trade_id::text
          OR (r.side='SELL' AND EXISTS(SELECT 1 FROM first_rise_j_sell_allocation a
              WHERE a.order_request_id=r.order_request_id AND a.trade_id=b.trade_id AND a.epoch_id=b.epoch_id))
        JOIN live_broker_order o ON o.order_request_id=r.order_request_id
        WHERE b.trade_id=%s AND o.broker_order_id=%s
          AND r.strategy_instance_id='FIRST_RISE_J_V1.3' AND o.strategy_instance_id=r.strategy_instance_id
          AND r.execution_stock_code=b.stock_code AND o.execution_stock_code=b.stock_code AND o.side=r.side''',
        (trade_id, broker_order_id))
    owner = q.fetchone()
    if owner is None:
        raise ValueError('FIRST_RISE_CHECKPOINT_OWNERSHIP_REQUIRED')
    epoch_id, stock, side, order_quantity = owner
    q.execute('''INSERT INTO first_rise_j_live_cost(trade_id,epoch_id) VALUES(%s,%s)
        ON CONFLICT(trade_id) DO NOTHING''', (trade_id, epoch_id))
    q.execute('''SELECT cost_trade_id,provisional_applied_at FROM first_rise_j_live_cost
        WHERE trade_id=%s FOR UPDATE''', (trade_id,))
    cost_id, closed = q.fetchone()
    q.execute('''SELECT cost_trade_id,stock_code,side,delta_quantity,delta_amount,broker_event_time
        FROM first_rise_j_live_checkpoint_allocation WHERE broker_order_id=%s AND checkpoint_version=%s AND cost_trade_id=%s''',
        (broker_order_id, version,cost_id))
    prior = q.fetchone()
    if prior:
        if prior != (cost_id, stock, side, quantity, amount, at):
            raise ValueError('FIRST_RISE_CHECKPOINT_IDEMPOTENCY_CONFLICT')
        return False
    if closed is not None:
        raise ValueError('FIRST_RISE_CLOSED_FILL_SET_CHANGED')
    if side=='SELL':
        q.execute('''SELECT a.trade_id,a.planned_quantity FROM first_rise_j_sell_allocation a
            JOIN live_broker_order o ON o.order_request_id=a.order_request_id WHERE o.broker_order_id=%s''',(broker_order_id,))
        plans=dict(q.fetchall())
        if plans:
            q.execute('''SELECT COALESCE(sum(delta_quantity),0) FROM first_rise_j_live_checkpoint_allocation
                WHERE broker_order_id=%s AND cost_trade_id=%s''',(broker_order_id,cost_id))
            if trade_id not in plans or q.fetchone()[0]+quantity>plans[trade_id]:
                raise ValueError('FIRST_RISE_CHECKPOINT_EXCEEDS_LOT_PLAN')
    q.execute('''SELECT COALESCE(sum(delta_quantity),0) FROM first_rise_j_live_checkpoint_allocation
        WHERE broker_order_id=%s''', (broker_order_id,))
    if q.fetchone()[0]+quantity > order_quantity:
        raise ValueError('FIRST_RISE_CHECKPOINT_EXCEEDS_ORDER')
    q.execute('''INSERT INTO first_rise_j_live_checkpoint_allocation
        (broker_order_id,checkpoint_version,cost_trade_id,stock_code,side,delta_quantity,delta_amount,broker_event_time)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
        (broker_order_id, version, cost_id, stock, side, quantity, amount, at))
    prefix = {'BUY': 'buy', 'SELL': 'sell'}[side]
    q.execute(f'''UPDATE first_rise_j_live_cost SET {prefix}_quantity={prefix}_quantity+%s,
        {prefix}_amount={prefix}_amount+%s WHERE cost_trade_id=%s''', (quantity, amount, cost_id))
    return True


def close_actual(q, *, trade_id, at, rates=ProvisionalRates()):
    """Called after actual position CLOSE; all owned requests must be terminal."""
    _lock(q)
    q.execute('''SELECT cost_trade_id,buy_quantity,sell_quantity,buy_amount,sell_amount,provisional_applied_at
        FROM first_rise_j_live_cost WHERE trade_id=%s FOR UPDATE''', (trade_id,))
    row = q.fetchone()
    if row is None or row[1] <= 0 or row[1] != row[2]:
        raise ValueError('FIRST_RISE_POSITION_NOT_CLOSED')
    cost_id, _, _, buy, sell, applied = row
    if applied is not None:
        return False
    q.execute('''SELECT EXISTS(SELECT 1 FROM live_order_request r
        LEFT JOIN live_broker_order o ON o.order_request_id=r.order_request_id
        WHERE r.strategy_instance_id='FIRST_RISE_J_V1.3' AND r.detail->>'first_rise_trade_id'=%s
          AND r.status NOT IN ('FILLED','REJECTED','CANCELLED')
          AND (r.side='BUY' OR NOT EXISTS(SELECT 1 FROM first_rise_j_sell_allocation a
              WHERE a.order_request_id=r.order_request_id AND a.trade_id=%s
                AND a.planned_quantity <= COALESCE((SELECT sum(x.delta_quantity)
                    FROM first_rise_j_live_checkpoint_allocation x
                    JOIN first_rise_j_live_cost cost ON cost.cost_trade_id=x.cost_trade_id
                    WHERE x.broker_order_id=o.broker_order_id AND cost.trade_id=a.trade_id),0))))''',
        (str(trade_id),trade_id))
    if q.fetchone()[0]:
        raise ValueError('FIRST_RISE_PENDING_ORDER_AT_CLOSE')
    costs = provisional_costs(buy, sell, rates)
    gross = sell-buy
    net = gross-sum(costs)
    key = f'FIRST_RISE|{trade_id}|PROVISIONAL_V1.8'
    evidence = rates.evidence()
    q.execute('''UPDATE first_rise_j_live_cost SET gross_realized_pnl=%s,
        provisional_buy_fee=%s,provisional_sell_fee=%s,provisional_sell_tax=%s,provisional_other_cost=%s,
        provisional_net_realized_pnl=%s,provisional_applied_at=%s,provisional_key=%s,rate_evidence=%s
        WHERE cost_trade_id=%s''', (gross, *costs, net, at, key, Jsonb(evidence), cost_id))
    _settle(q, trade_id=trade_id, key=key, amount=net, at=at,
            evidence={**evidence, 'settlement_type': 'PROVISIONAL', 'gross_pnl': str(gross)})
    settle_final_costs(q, cost_trade_id=cost_id, at=at)
    return True


def settle_final_costs(q, *, cost_trade_id, at):
    """Use only globally allocated FIRST_RISE slices, across every fill day/side."""
    _lock(q)
    q.execute('''SELECT trade_id,provisional_applied_at,settlement_delta_applied_at,
        provisional_buy_fee+provisional_sell_fee+provisional_sell_tax+provisional_other_cost,
        provisional_net_realized_pnl FROM first_rise_j_live_cost WHERE cost_trade_id=%s FOR UPDATE''',
        (cost_trade_id,))
    row = q.fetchone()
    if row is None or row[1] is None or row[2] is not None:
        return False
    trade_id, _, _, provisional, net = row
    q.execute('''WITH owned AS (
        SELECT broker_event_time::date AS day,stock_code,side,sum(delta_amount) AS amount
        FROM first_rise_j_live_checkpoint_allocation WHERE cost_trade_id=%s GROUP BY 1,2,3)
        SELECT a.buy_fee,a.sell_fee,a.sell_tax,a.other_cost,s.status,a.fill_notional,o.amount
        FROM owned o LEFT JOIN broker_shared_cost_allocation a
          ON a.trade_date=o.day AND a.execution_stock_code=o.stock_code AND a.side=o.side
         AND a.family='FIRST_RISE' AND a.live_trade_id=%s
        LEFT JOIN broker_shared_cost_snapshot s
          ON s.trade_date=o.day AND s.execution_stock_code=o.stock_code''', (cost_trade_id, cost_trade_id))
    parts = q.fetchall()
    if not parts or any(p[4] != 'FINALIZED_BY_STABLE_RECHECK' or p[5] != p[6] for p in parts):
        return False
    actual = tuple(sum((p[i] for p in parts), Decimal(0)) for i in range(4))
    delta = provisional-sum(actual)
    key = f'FIRST_RISE|{trade_id}|ACTUAL_COST_DELTA_V1.8'
    q.execute('''UPDATE first_rise_j_live_cost SET actual_buy_fee=%s,actual_sell_fee=%s,
        actual_sell_tax=%s,actual_other_cost=%s,actual_cost_finalized_at=%s,settlement_delta=%s,
        settlement_delta_applied_at=%s,settlement_key=%s,final_net_realized_pnl=%s WHERE cost_trade_id=%s''',
        (*actual, at, delta, at, key, net+delta, cost_trade_id))
    _settle(q, trade_id=trade_id, key=key, amount=delta, at=at,
            evidence={'settlement_type': 'ACTUAL_COST_DELTA', 'ownership': 'FIRST_RISE',
                      'provisional_cost': str(provisional), 'actual_cost': str(sum(actual))})
    return True
