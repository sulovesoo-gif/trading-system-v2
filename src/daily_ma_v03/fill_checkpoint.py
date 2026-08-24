"""Daily MA KIS cumulative-fill checkpoint, not a synthetic per-fill ledger."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import NAMESPACE_URL, uuid5

class CheckpointStatus(str,Enum): ACTIVE='ACTIVE'; BROKER_FILL_CHECKPOINT_REGRESSION='BROKER_FILL_CHECKPOINT_REGRESSION'
@dataclass(frozen=True)
class FillCheckpoint:
 broker_order_id:str;broker_order_number:str;cumulative_filled_qty:int;cumulative_filled_amount:Decimal;last_avg_fill_price:Decimal;last_broker_event_time:datetime|None;version:int=0;status:CheckpointStatus=CheckpointStatus.ACTIVE
@dataclass(frozen=True)
class CheckpointDelta:
 quantity:int;amount:Decimal;new_checkpoint:FillCheckpoint|None;status:str

def advance_checkpoint(*,stored:FillCheckpoint|None,broker_order_id:str,broker_order_number:str,cumulative_quantity:int,cumulative_amount:Decimal,average_price:Decimal,event_time:datetime|None)->CheckpointDelta:
 if cumulative_quantity<0 or cumulative_amount<0: raise ValueError('BROKER_CUMULATIVE_VALUE_INVALID')
 if stored is not None and stored.status is not CheckpointStatus.ACTIVE:return CheckpointDelta(0,Decimal('0'),stored,'BROKER_FILL_CHECKPOINT_REGRESSION')
 old_qty=stored.cumulative_filled_qty if stored else 0;old_amt=stored.cumulative_filled_amount if stored else Decimal('0')
 if cumulative_quantity<old_qty or cumulative_amount<old_amt:
  blocked=FillCheckpoint(broker_order_id,broker_order_number,old_qty,old_amt,stored.last_avg_fill_price if stored else Decimal('0'),event_time,stored.version if stored else 0,CheckpointStatus.BROKER_FILL_CHECKPOINT_REGRESSION)
  return CheckpointDelta(0,Decimal('0'),blocked,'BROKER_FILL_CHECKPOINT_REGRESSION')
 if cumulative_quantity==old_qty and cumulative_amount==old_amt:return CheckpointDelta(0,Decimal('0'),stored,'DUPLICATE')
 if cumulative_quantity==old_qty or cumulative_amount==old_amt:return CheckpointDelta(0,Decimal('0'),stored,'BROKER_FILL_CHECKPOINT_INCONSISTENT')
 next_state=FillCheckpoint(broker_order_id,broker_order_number,cumulative_quantity,cumulative_amount,average_price,event_time,(stored.version if stored else 0)+1)
 return CheckpointDelta(cumulative_quantity-old_qty,cumulative_amount-old_amt,next_state,'ADVANCED')

class InMemoryFillCheckpointStore:
 def __init__(self):self.checkpoints={};self.allocations=[]
 def apply(self,**kwargs):
  delta=advance_checkpoint(stored=self.checkpoints.get(kwargs['broker_order_id']),**kwargs)
  if delta.new_checkpoint is not None:self.checkpoints[kwargs['broker_order_id']]=delta.new_checkpoint
  if delta.status=='ADVANCED':self.allocations.append((kwargs['broker_order_id'],delta.new_checkpoint.version,delta.quantity,delta.amount))
  return delta

class PostgresDailyMaFillCheckpointStore:
 """Applies only checkpoint deltas; it never manufactures broker fill IDs."""
 def __init__(self,connection_factory):self.connection_factory=connection_factory
 def apply(self,*,broker_order_id,broker_order_number,ownership_id,stock_code,side,cumulative_quantity,cumulative_amount,average_price,event_time):
  with self.connection_factory() as c,c.cursor() as q:
   q.execute("SELECT cumulative_filled_qty,cumulative_filled_amount,last_avg_fill_price,last_broker_event_time,version,checkpoint_status FROM daily_strategy_live_fill_checkpoint WHERE broker_order_id=%s FOR UPDATE",(broker_order_id,));row=q.fetchone()
   stored=None if row is None else FillCheckpoint(broker_order_id,broker_order_number,int(row[0]),Decimal(row[1]),Decimal(row[2]),row[3],int(row[4]),CheckpointStatus(row[5]))
   delta=advance_checkpoint(stored=stored,broker_order_id=broker_order_id,broker_order_number=broker_order_number,cumulative_quantity=cumulative_quantity,cumulative_amount=Decimal(cumulative_amount),average_price=Decimal(average_price),event_time=event_time)
   if delta.status=='DUPLICATE':c.commit();return delta
   state=delta.new_checkpoint
   q.execute("""INSERT INTO daily_strategy_live_fill_checkpoint(broker_order_id,broker_order_number,cumulative_filled_qty,cumulative_filled_amount,last_avg_fill_price,last_broker_event_time,version,checkpoint_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (broker_order_id) DO UPDATE SET broker_order_number=EXCLUDED.broker_order_number,cumulative_filled_qty=EXCLUDED.cumulative_filled_qty,cumulative_filled_amount=EXCLUDED.cumulative_filled_amount,last_avg_fill_price=EXCLUDED.last_avg_fill_price,last_broker_event_time=EXCLUDED.last_broker_event_time,version=EXCLUDED.version,checkpoint_status=EXCLUDED.checkpoint_status,updated_at=CURRENT_TIMESTAMP""",(broker_order_id,broker_order_number,state.cumulative_filled_qty,state.cumulative_filled_amount,state.last_avg_fill_price,state.last_broker_event_time,state.version,state.status.value))
   if delta.status!='ADVANCED':c.commit();return delta
   allocation_id=str(uuid5(NAMESPACE_URL,f'daily-ma-checkpoint|{broker_order_id}|{state.version}'))
   q.execute("""INSERT INTO daily_strategy_live_checkpoint_allocation(allocation_id,broker_order_id,checkpoint_version,ownership_id,stock_code,side,delta_quantity,delta_amount,broker_event_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (broker_order_id,checkpoint_version) DO NOTHING""",(allocation_id,broker_order_id,state.version,ownership_id,stock_code,side,delta.quantity,delta.amount,event_time))
   q.execute("SELECT quantity,average_cost,realized_pnl,version FROM execution_logical_position WHERE ownership_type='LIVE' AND ownership_id=%s AND stock_code=%s FOR UPDATE",(ownership_id,stock_code));pos=q.fetchone() or (0,Decimal('0'),Decimal('0'),0)
   quantity,average,realized,version=int(pos[0]),Decimal(pos[1]),Decimal(pos[2]),int(pos[3])
   if side=='SELL' and delta.quantity>quantity:raise ValueError('OWNERSHIP_REQUIRED')
   if side=='BUY': next_qty=quantity+delta.quantity;next_avg=(average*quantity+delta.amount)/next_qty;next_realized=realized
   else: next_qty=quantity-delta.quantity;next_avg=average if next_qty else Decimal('0');next_realized=realized+delta.amount-average*delta.quantity
   q.execute("""INSERT INTO execution_logical_position(ownership_type,ownership_id,stock_code,quantity,average_cost,realized_pnl,last_fill_at,version)
                VALUES ('LIVE',%s,%s,%s,%s,%s,%s,1) ON CONFLICT (ownership_type,ownership_id,stock_code) DO UPDATE SET quantity=EXCLUDED.quantity,average_cost=EXCLUDED.average_cost,realized_pnl=EXCLUDED.realized_pnl,last_fill_at=EXCLUDED.last_fill_at,version=execution_logical_position.version+1,updated_at=CURRENT_TIMESTAMP""",(ownership_id,stock_code,next_qty,next_avg,next_realized,event_time))
   c.commit();return delta
