"""Daily MA KIS cumulative-fill checkpoint, not a synthetic per-fill ledger."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

class CheckpointStatus(str,Enum): ACTIVE='ACTIVE'; BROKER_FILL_CHECKPOINT_REGRESSION='BROKER_FILL_CHECKPOINT_REGRESSION'
@dataclass(frozen=True)
class FillCheckpoint:
 broker_order_id:str;broker_order_number:str;cumulative_filled_qty:int;cumulative_filled_amount:Decimal;last_avg_fill_price:Decimal;last_broker_event_time:datetime|None;version:int=0;status:CheckpointStatus=CheckpointStatus.ACTIVE
@dataclass(frozen=True)
class CheckpointDelta:
 quantity:int;amount:Decimal;new_checkpoint:FillCheckpoint|None;status:str

def advance_checkpoint(*,stored:FillCheckpoint|None,broker_order_id:str,broker_order_number:str,cumulative_quantity:int,cumulative_amount:Decimal,average_price:Decimal,event_time:datetime|None)->CheckpointDelta:
 if cumulative_quantity<0 or cumulative_amount<0: raise ValueError('BROKER_CUMULATIVE_VALUE_INVALID')
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
