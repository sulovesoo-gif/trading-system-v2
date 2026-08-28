from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL,uuid5
from src.daily_ma_v03.fill_checkpoint import FillCheckpoint,CheckpointStatus,advance_checkpoint

class PostgresMinuteMaFillCheckpointStore:
    def __init__(self,connection_factory):self.connection_factory=connection_factory
    def apply(self,*,order,cumulative_quantity,cumulative_amount,average_price,event_time):
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("SELECT cumulative_filled_qty,cumulative_filled_amount,last_avg_fill_price,last_broker_event_time,version,checkpoint_status FROM minute_ma_live_fill_checkpoint WHERE broker_order_id=%s FOR UPDATE",(order.broker_order_id,));r=q.fetchone()
            stored=None if r is None else FillCheckpoint(order.broker_order_id,order.broker_order_number,int(r[0]),Decimal(r[1]),Decimal(r[2]),r[3],int(r[4]),CheckpointStatus(r[5]))
            delta=advance_checkpoint(stored=stored,broker_order_id=order.broker_order_id,broker_order_number=order.broker_order_number,
              cumulative_quantity=cumulative_quantity,cumulative_amount=Decimal(cumulative_amount),average_price=Decimal(average_price),event_time=event_time)
            if delta.status=='DUPLICATE':c.commit();return delta
            state=delta.new_checkpoint
            q.execute("""INSERT INTO minute_ma_live_fill_checkpoint(broker_order_id,broker_order_number,cumulative_filled_qty,
              cumulative_filled_amount,last_avg_fill_price,last_broker_event_time,version,checkpoint_status)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(broker_order_id) DO UPDATE SET
              broker_order_number=EXCLUDED.broker_order_number,cumulative_filled_qty=EXCLUDED.cumulative_filled_qty,
              cumulative_filled_amount=EXCLUDED.cumulative_filled_amount,last_avg_fill_price=EXCLUDED.last_avg_fill_price,
              last_broker_event_time=EXCLUDED.last_broker_event_time,version=EXCLUDED.version,
              checkpoint_status=EXCLUDED.checkpoint_status,updated_at=CURRENT_TIMESTAMP""",
              (order.broker_order_id,order.broker_order_number,state.cumulative_filled_qty,state.cumulative_filled_amount,
               state.last_avg_fill_price,state.last_broker_event_time,state.version,state.status.value))
            if delta.status!='ADVANCED':c.commit();return delta
            q.execute("""SELECT i.intent_type,i.minute_live_trade_id,i.minute_path_id,i.capital_at_signal,
              o.operation_id,COALESCE(po.capital_epoch_no,o.capital_epoch_no),i.minute_policy_path_id,
              i.minute_policy_operation_id,
              i.underlying_entry_reference_price,i.stop_threshold_price,i.stop_policy
              FROM minute_ma_live_intent i
              JOIN minute_ma_live_order_link l USING(intent_id)
              LEFT JOIN minute_ma_operation o ON i.minute_policy_path_id IS NULL
                AND o.minute_path_id=i.minute_path_id AND o.effective_to IS NULL
              LEFT JOIN minute_ma_policy_operation po
                ON po.minute_policy_operation_id=i.minute_policy_operation_id
              WHERE l.broker_order_id=%s FOR UPDATE""",(order.broker_order_id,));intent=q.fetchone()
            if intent is None:raise ValueError('MINUTE_MA_INTENT_REQUIRED')
            intent_type,trade_id,path_id,capital,operation_id,epoch,policy_path_id,policy_operation_id,anchor,threshold,stop_policy=intent
            if epoch is None:raise ValueError('MINUTE_MA_OPERATION_REQUIRED')
            if intent_type=='ENTRY' and trade_id is None:
                ownership=f'MINUTE_MA_TRADE:{order.intent_id}'
                q.execute("""INSERT INTO minute_ma_live_trade(minute_path_id,operation_id,capital_epoch_no,ownership_id,
                  trade_status,capital_at_signal,minute_policy_path_id,underlying_entry_reference_price,
                  stop_threshold_price,stop_policy,minute_policy_operation_id)
                  VALUES(%s,%s,%s,%s,'OPEN',%s,%s,%s,%s,%s,%s)
                  RETURNING minute_live_trade_id""",
                  (path_id,operation_id,epoch,ownership,capital,policy_path_id,anchor,threshold,stop_policy,
                   policy_operation_id));trade_id=q.fetchone()[0]
                q.execute("UPDATE minute_ma_live_intent SET minute_live_trade_id=%s WHERE intent_id=%s",(trade_id,order.intent_id))
            else:
                q.execute("SELECT ownership_id FROM minute_ma_live_trade WHERE minute_live_trade_id=%s FOR UPDATE",(trade_id,));x=q.fetchone()
                if x is None:raise ValueError('MINUTE_MA_LIVE_TRADE_REQUIRED')
                ownership=x[0]
            allocation_id=str(uuid5(NAMESPACE_URL,f'minute-ma-checkpoint|{order.broker_order_id}|{state.version}'))
            q.execute("""INSERT INTO minute_ma_live_checkpoint_allocation(allocation_id,broker_order_id,checkpoint_version,
              minute_live_trade_id,ownership_id,stock_code,side,delta_quantity,delta_amount,broker_event_time)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(broker_order_id,checkpoint_version) DO NOTHING""",
              (allocation_id,order.broker_order_id,state.version,trade_id,ownership,order.stock_code,order.side,delta.quantity,delta.amount,event_time))
            q.execute("SELECT quantity,average_cost,realized_pnl FROM execution_logical_position WHERE ownership_type='MINUTE_MA' AND ownership_id=%s AND stock_code=%s FOR UPDATE",(ownership,order.stock_code));pos=q.fetchone() or (0,Decimal('0'),Decimal('0'))
            qty,avg,realized=int(pos[0]),Decimal(pos[1]),Decimal(pos[2])
            if order.side=='SELL' and delta.quantity>qty:raise ValueError('OWNERSHIP_REQUIRED')
            if order.side=='BUY':next_qty=qty+delta.quantity;next_avg=(avg*qty+delta.amount)/next_qty;next_realized=realized
            else:next_qty=qty-delta.quantity;next_avg=avg if next_qty else Decimal('0');next_realized=realized+delta.amount-avg*delta.quantity
            q.execute("""INSERT INTO execution_logical_position(ownership_type,ownership_id,stock_code,quantity,average_cost,
              realized_pnl,last_fill_at,version) VALUES('MINUTE_MA',%s,%s,%s,%s,%s,%s,1)
              ON CONFLICT(ownership_type,ownership_id,stock_code) DO UPDATE SET quantity=EXCLUDED.quantity,
              average_cost=EXCLUDED.average_cost,realized_pnl=EXCLUDED.realized_pnl,last_fill_at=EXCLUDED.last_fill_at,
              version=execution_logical_position.version+1,updated_at=CURRENT_TIMESTAMP""",
              (ownership,order.stock_code,next_qty,next_avg,next_realized,event_time))
            field='entry_filled_amount' if order.side=='BUY' else 'exit_filled_amount'
            q.execute(f"UPDATE minute_ma_live_trade SET {field}={field}+%s,updated_at=CURRENT_TIMESTAMP WHERE minute_live_trade_id=%s",(delta.amount,trade_id))
            status='FILLED' if cumulative_quantity>=order.quantity else 'PARTIALLY_FILLED'
            q.execute("UPDATE live_broker_order SET status=%s WHERE broker_order_id=%s",(status,order.broker_order_id))
            q.execute("UPDATE live_order_request SET status=%s WHERE order_request_id=(SELECT order_request_id FROM live_broker_order WHERE broker_order_id=%s)",(status,order.broker_order_id))
            q.execute("UPDATE minute_ma_live_intent SET lifecycle_status=%s,updated_at=CURRENT_TIMESTAMP WHERE intent_id=%s",(status,order.intent_id))
            if order.side=='BUY':
                q.execute("""UPDATE minute_ma_live_capital_reservation SET consumed_amount=LEAST(reserved_amount,consumed_amount+%s),
                  reservation_status=CASE WHEN consumed_amount+%s>=reserved_amount THEN 'CONSUMED' ELSE 'PARTIALLY_CONSUMED' END,
                  updated_at=CURRENT_TIMESTAMP WHERE intent_id=%s""",(delta.amount,delta.amount,order.intent_id))
            elif next_qty==0:
                q.execute("UPDATE minute_ma_live_trade SET trade_status='CLOSED',updated_at=CURRENT_TIMESTAMP WHERE minute_live_trade_id=%s",(trade_id,))
                q.execute("""INSERT INTO minute_ma_live_broker_cost_snapshot(broker_cost_snapshot_id,trade_date,execution_stock_code,
                  broker_snapshot_at,finalization_status) VALUES(%s,%s,%s,CURRENT_TIMESTAMP,'PENDING_BROKER_COST')
                  ON CONFLICT(trade_date,execution_stock_code) DO NOTHING""",
                  (str(uuid5(NAMESPACE_URL,f'minute-ma-cost|{event_time.date()}|{order.stock_code}')),event_time.date(),order.stock_code))
            c.commit();return delta
