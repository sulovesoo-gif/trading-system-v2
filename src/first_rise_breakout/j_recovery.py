"""KIS cumulative evidence -> owned checkpoint -> CLOSE, without synthetic fills."""
import logging
from datetime import datetime
from decimal import Decimal

from src.daily_ma_v03.fill_checkpoint import FillCheckpoint,CheckpointStatus,advance_checkpoint
from .j_cost import record_checkpoint,close_actual,retain_exit_recovery
from .j_execution import STRATEGY_ID

LOGGER=logging.getLogger(__name__)


class JRecovery:
    def __init__(self,pool,history_lookup):self.pool,self.history_lookup=pool,history_lookup

    def pending(self):
        with self.pool.connection() as c,c.cursor() as q:
            q.execute('''SELECT o.broker_order_id,o.broker_order_number,o.execution_stock_code,o.side,o.quantity,
                r.execution_target_time::date FROM live_broker_order o
                JOIN live_order_request r ON r.order_request_id=o.order_request_id
                JOIN first_rise_j_live_intent i ON i.order_request_id=r.order_request_id
                WHERE o.strategy_instance_id=%s AND r.strategy_instance_id=%s
                  AND o.status IN ('SUBMITTING','ACCEPTED','PARTIALLY_FILLED','UNKNOWN_BROKER_STATE')
                ORDER BY o.created_at''',(STRATEGY_ID,STRATEGY_ID))
            return q.fetchall()

    def poll(self,*,at):
        advanced=unresolved=errors=0
        for broker,number,stock,side,qty,day in self.pending():
            try:
                if not number:
                    with self.pool.connection() as c,c.cursor() as q:
                        q.execute("SELECT detail->>'order_number',detail->>'rt_cd' FROM live_broker_order_audit WHERE broker_order_id=%s AND event_type='FIRST_RISE_POST_RESPONSE' ORDER BY audit_id DESC LIMIT 1",(broker,))
                        response=q.fetchone()
                        number=response[0] if response else None
                    if response and response[1] and response[1]!='0':
                        with self.pool.connection() as c,c.transaction(),c.cursor() as q:
                            q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
                            q.execute("UPDATE live_broker_order SET status='REJECTED' WHERE broker_order_id=%s AND strategy_instance_id=%s AND status IN ('SUBMITTING','UNKNOWN_BROKER_STATE') RETURNING order_request_id",(broker,STRATEGY_ID))
                            rejected=q.fetchone()
                            if rejected:q.execute("UPDATE live_order_request SET status='REJECTED' WHERE order_request_id=%s",(rejected[0],))
                            if rejected and side=='SELL':
                                retain_exit_recovery(q,request_id=rejected[0],broker_id=broker,at=at,source='DURABLE_POST_RESPONSE')
                        continue
                # Quantity/time coincidence cannot prove ownership against other
                # strategies or manual orders. Missing broker identity stays unknown.
                if not number:
                    unresolved+=1;continue
                records=self.history_lookup.orders_for_day(order_date=day,stock_code=stock,side=side,order_number=number)
                matches=[r for r in records if r.order_number==number and r.stock_code==stock
                         and r.side==side and r.order_quantity==qty and r.order_date==day.strftime('%Y%m%d')]
                if len(matches)!=1:
                    unresolved+=1;continue
                advanced+=int(self.apply(broker,record=matches[0],at=at)=='ADVANCED')
            except Exception as error:
                errors+=1
                LOGGER.exception('FIRST_RISE_RECOVERY_ERROR broker_order_id=%s exception_type=%s',broker,type(error).__name__)
        return {'advanced':advanced,'unresolved':unresolved,'errors':errors}

    def apply(self,broker_order_id,*,record,at):
        with self.pool.connection() as c,c.transaction(),c.cursor() as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
            q.execute('''SELECT o.order_request_id,i.trade_id,o.execution_stock_code,o.side,o.quantity,o.broker_order_number,
                r.execution_target_time::date FROM live_broker_order o
                JOIN live_order_request r ON r.order_request_id=o.order_request_id
                JOIN first_rise_j_live_intent i ON i.order_request_id=r.order_request_id
                WHERE o.broker_order_id=%s AND o.strategy_instance_id=%s AND r.strategy_instance_id=%s
                  AND r.detail->>'first_rise_trade_id'=i.trade_id::text FOR UPDATE OF o''',
                (broker_order_id,STRATEGY_ID,STRATEGY_ID))
            owner=q.fetchone()
            if owner is None:raise ValueError('FIRST_RISE_RECOVERY_OWNERSHIP_REQUIRED')
            request,trade,stock,side,qty,known,day=owner
            if (not record.order_number or stock!=record.stock_code or side!=record.side or qty!=record.order_quantity
                    or record.order_date!=day.strftime('%Y%m%d') or (known and known!=record.order_number)):
                raise ValueError('FIRST_RISE_BROKER_IDENTITY_MISMATCH')
            q.execute('''SELECT 1 FROM live_broker_order o JOIN live_order_request r ON r.order_request_id=o.order_request_id
                WHERE o.broker_order_number=%s AND r.execution_target_time::date=%s AND o.broker_order_id<>%s LIMIT 1''',
                (record.order_number,day,broker_order_id))
            if q.fetchone():raise ValueError('FIRST_RISE_BROKER_ORDER_ALREADY_OWNED')
            q.execute('SELECT cumulative_quantity,cumulative_amount,average_price,event_time,version,status FROM first_rise_j_fill_checkpoint WHERE broker_order_id=%s FOR UPDATE',(broker_order_id,))
            old=q.fetchone()
            stored=FillCheckpoint(str(broker_order_id),record.order_number,*old[:5],CheckpointStatus(old[5])) if old else None
            if record.total_filled_quantity>qty:raise ValueError('FIRST_RISE_FILL_EXCEEDS_REQUEST')
            delta=advance_checkpoint(stored=stored,broker_order_id=str(broker_order_id),broker_order_number=record.order_number,
                cumulative_quantity=record.total_filled_quantity,cumulative_amount=record.total_filled_amount,
                average_price=record.average_fill_price,event_time=at)
            if delta.status not in ('ADVANCED','DUPLICATE'):
                raise ValueError(delta.status)
            status=('FILLED' if record.total_filled_quantity==qty else 'CANCELLED' if record.cancelled and record.remaining_quantity==0
                    else 'REJECTED' if record.rejected_quantity>0 and record.total_filled_quantity+record.rejected_quantity>=qty and record.remaining_quantity==0 else
                    'PARTIALLY_FILLED' if record.total_filled_quantity else 'ACCEPTED')
            q.execute('UPDATE live_broker_order SET status=%s,broker_order_number=%s WHERE broker_order_id=%s',
                (status,record.order_number,broker_order_id))
            q.execute('UPDATE live_order_request SET status=%s WHERE order_request_id=%s',(status,request))
            q.execute('''SELECT 1 FROM first_rise_j_live_intent i JOIN first_rise_j_market_signal s
                ON s.market_signal_id=i.market_signal_id WHERE i.order_request_id=%s
                AND s.entry_evidence->>'formula_version'='FIRST_RISE_J_V2.0_FORMULA_20261004' ''',(request,))
            if q.fetchone():
                q.execute('''INSERT INTO first_rise_v2_order_observation(broker_order_id,first_fill_observed_at,
                    terminal_observed_at,had_partial_fill,cumulative_quantity,cumulative_amount)
                    VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(broker_order_id) DO UPDATE SET
                    first_fill_observed_at=COALESCE(first_rise_v2_order_observation.first_fill_observed_at,EXCLUDED.first_fill_observed_at),
                    terminal_observed_at=COALESCE(first_rise_v2_order_observation.terminal_observed_at,EXCLUDED.terminal_observed_at),
                    had_partial_fill=first_rise_v2_order_observation.had_partial_fill OR EXCLUDED.had_partial_fill,
                    cumulative_quantity=EXCLUDED.cumulative_quantity,cumulative_amount=EXCLUDED.cumulative_amount''',
                    (broker_order_id,at if record.total_filled_quantity else None,
                     at if status in ('FILLED','CANCELLED','REJECTED') else None,
                     0<record.total_filled_quantity<qty,record.total_filled_quantity,record.total_filled_amount))
            if side=='SELL' and status in ('REJECTED','CANCELLED') and record.total_filled_quantity<qty:
                LOGGER.warning('FIRST_RISE_EXIT_RECOVERY broker_order_id=%s remaining_request_qty=%s',
                             broker_order_id,qty-record.total_filled_quantity)
            if delta.status=='ADVANCED':
                checkpoint=delta.new_checkpoint
                q.execute('''INSERT INTO first_rise_j_fill_checkpoint(broker_order_id,cumulative_quantity,cumulative_amount,
                    average_price,event_time,version,status) VALUES(%s,%s,%s,%s,%s,%s,'ACTIVE')
                    ON CONFLICT(broker_order_id) DO UPDATE SET cumulative_quantity=EXCLUDED.cumulative_quantity,
                    cumulative_amount=EXCLUDED.cumulative_amount,average_price=EXCLUDED.average_price,
                    event_time=EXCLUDED.event_time,version=EXCLUDED.version''',
                    (broker_order_id,checkpoint.cumulative_filled_qty,checkpoint.cumulative_filled_amount,
                     checkpoint.last_avg_fill_price,at,checkpoint.version))
                # Cost ownership dates are actual broker order/fill day, not a
                # later restart polling date. KIS supplies cumulative evidence.
                broker_time=datetime.strptime(record.order_date+record.order_time,'%Y%m%d%H%M%S')
                if side=='SELL':
                    allocate_sell_checkpoint(q,request=request,broker=broker_order_id,version=checkpoint.version,
                        quantity=delta.quantity,amount=delta.amount,at=broker_time)
                else:
                    record_checkpoint(q,trade_id=trade,broker_order_id=broker_order_id,version=checkpoint.version,
                        quantity=delta.quantity,amount=delta.amount,at=broker_time)
            q.execute('''SELECT c.trade_id FROM first_rise_j_live_cost c WHERE c.buy_quantity>0
                AND c.buy_quantity=c.sell_quantity AND (c.trade_id=%s OR EXISTS(
                    SELECT 1 FROM first_rise_j_sell_allocation a WHERE a.order_request_id=%s AND a.trade_id=c.trade_id))''',(trade,request))
            for (closed_trade,) in q.fetchall():close_actual(q,trade_id=closed_trade,at=at)
            if side=='SELL' and status=='REJECTED':
                retain_exit_recovery(q,request_id=request,broker_id=broker_order_id,at=at,source='KIS_ORDER_HISTORY')
            if status in ('FILLED','REJECTED','CANCELLED'):
                q.execute("UPDATE first_rise_j_cancel_request SET status='CONFIRMED',terminal_confirmed_at=%s WHERE broker_order_id=%s",(at,broker_order_id))
            return delta.status


def allocate_sell_checkpoint(q,*,request,broker,version,quantity,amount,at):
    """FIFO over immutable owned plan quantities; final slice owns rounding dust."""
    q.execute('''SELECT a.trade_id,a.planned_quantity-COALESCE((SELECT sum(x.delta_quantity)
        FROM first_rise_j_live_checkpoint_allocation x JOIN first_rise_j_live_cost c ON c.cost_trade_id=x.cost_trade_id
        WHERE x.broker_order_id=%s AND c.trade_id=a.trade_id),0)
        FROM first_rise_j_sell_allocation a WHERE a.order_request_id=%s ORDER BY a.allocation_order''',(broker,request))
    lots=q.fetchall()
    slices=[];remaining=quantity
    for trade,available in lots:
        take=min(remaining,available)
        if take>0:slices.append((trade,take));remaining-=take
    if remaining:raise ValueError('FIRST_RISE_ALLOCATION_EXCEEDS_OWNED_PLAN')
    allocated=Decimal(0)
    for index,(trade,take) in enumerate(slices):
        part=amount-allocated if index==len(slices)-1 else (amount*Decimal(take)/quantity).quantize(Decimal('.00000001'))
        record_checkpoint(q,trade_id=trade,broker_order_id=broker,version=version,quantity=take,amount=part,at=at)
        allocated+=part
