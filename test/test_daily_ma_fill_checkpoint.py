import unittest
from datetime import datetime
from decimal import Decimal
from src.daily_ma_v03.fill_checkpoint import InMemoryFillCheckpointStore, CheckpointStatus
class DailyMaFillCheckpointTest(unittest.TestCase):
 def test_partial_duplicate_restart_final_and_regression(self):
  store=InMemoryFillCheckpointStore(); common=dict(broker_order_id='o',broker_order_number='123',average_price=Decimal('100'),event_time=datetime(2026,8,25,15,20))
  first=store.apply(**common,cumulative_quantity=3,cumulative_amount=Decimal('300'));dup=store.apply(**common,cumulative_quantity=3,cumulative_amount=Decimal('300'));final=store.apply(**common,cumulative_quantity=7,cumulative_amount=Decimal('710'))
  self.assertEqual((first.quantity,first.amount,dup.status,final.quantity,final.amount), (3,Decimal('300'),'DUPLICATE',4,Decimal('410')));self.assertEqual(len(store.allocations),2)
  regression=store.apply(**common,cumulative_quantity=6,cumulative_amount=Decimal('600'));self.assertEqual(regression.status,'BROKER_FILL_CHECKPOINT_REGRESSION');self.assertEqual(regression.new_checkpoint.status,CheckpointStatus.BROKER_FILL_CHECKPOINT_REGRESSION)
  self.assertEqual(store.apply(**common,cumulative_quantity=8,cumulative_amount=Decimal('800')).status,'BROKER_FILL_CHECKPOINT_REGRESSION');self.assertEqual(len(store.allocations),2)
