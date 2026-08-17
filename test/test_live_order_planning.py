from datetime import datetime,timedelta
import ast,unittest
from pathlib import Path
from src.live_intent.contracts import IntentStatus,IntentType,LiveIntent,RuntimeStatus
from src.live_order import AccountPolicy,InMemoryOrderPlanningStore,LiveOrderSafetyGate,OrderPlanner
ROOT=Path(__file__).resolve().parents[1]
def intent(instance='S1',at=datetime(2026,8,1,10)):
 return LiveIntent.build(strategy_instance_id=instance,strategy_code='S',strategy_version='1',code_commit=None,source_decision_id='00000000-0000-0000-0000-000000000001',intent_type=IntentType.ENTRY_INTENT,signal_stock_code='000660',signal_direction='SHORT',execution_stock_code='0197X0',execution_direction='LONG',signal_time=at,decision_time=at,execution_target_time=at+timedelta(minutes=1),reason_code='X',decision_evidence={},data_quality_status='PASS',runtime_state_before=RuntimeStatus.FLAT,runtime_state_after=RuntimeStatus.OPEN_SIMULATED,status=IntentStatus.CREATED)
class PlanningTest(unittest.TestCase):
 def setUp(self):
  self.store=InMemoryOrderPlanningStore();[self.store.ensure_account(x,1_000_000) for x in ('S1','S2','S3_3','S3_5')];self.planner=OrderPlanner(store=self.store,gate=LiveOrderSafetyGate(),policy=AccountPolicy(40_000_000,30_000_000,4_000_000),whitelist={'0197X0'})
 def test_independent_floor_reserve_and_idempotency(self):
  one,safe=self.planner.plan(intent=intent('S3_3'),reference_price=10000,now=datetime(2026,8,1,10,1),live_enabled=True,global_trade_yn='N');two,_=self.planner.plan(intent=intent('S3_5'),reference_price=20000,now=datetime(2026,8,1,10,1),live_enabled=True,global_trade_yn='N');again,_=self.planner.plan(intent=intent('S3_3'),reference_price=10000,now=datetime(2026,8,1,10,1),live_enabled=True,global_trade_yn='N');self.assertEqual(one.requested_quantity,99);self.assertEqual(two.requested_quantity,49);self.assertEqual(again.order_request_id,one.order_request_id);self.assertLess(self.store.account('S3_3').available_capital,1_000_000);self.assertEqual(self.store.account('S1').available_capital,1_000_000);self.assertFalse(safe.broker_send_eligible)
 def test_blocks_zero_stale_disabled_and_exit_position(self):
  self.assertEqual(self.planner.plan(intent=intent(),reference_price=2_000_000,now=datetime(2026,8,1,10,1),live_enabled=True,global_trade_yn='N')[1].reason,'QUANTITY_ZERO_BLOCK');self.assertEqual(self.planner.plan(intent=intent(),reference_price=10000,now=datetime(2026,8,1,10,5),live_enabled=True,global_trade_yn='N')[1].reason,'STALE_INTENT_BLOCKED');self.assertEqual(self.planner.plan(intent=intent(),reference_price=10000,now=datetime(2026,8,1,10,1),live_enabled=False,global_trade_yn='N')[1].reason,'LIVE_DISABLED_BLOCKED')
 def test_no_broker_dependency(self):
  for p in (ROOT/'src'/'live_order').glob('*.py'):
   mods=[a.name for n in ast.walk(ast.parse(p.read_text())) if isinstance(n,ast.Import) for a in n.names]+[n.module or '' for n in ast.walk(ast.parse(p.read_text())) if isinstance(n,ast.ImportFrom)]
   self.assertTrue(all(not any(x in m.lower() for x in ('broker','kis','order_service','collector','ntfy')) for m in mods))
if __name__=='__main__':unittest.main()
