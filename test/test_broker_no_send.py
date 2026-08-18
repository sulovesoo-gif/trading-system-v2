import ast,unittest
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path
from src.broker import *
ROOT=Path(__file__).resolve().parents[1]
def req(code='0197X0',side='BUY',qty=10):return SimpleNamespace(order_request_id='11111111-1111-1111-1111-111111111111',strategy_instance_id='S3_3',execution_stock_code=code,side=side,requested_quantity=qty,execution_target_time=datetime(2026,8,1,10))
class BrokerTest(unittest.TestCase):
 def test_no_send_payload_and_network_zero(self):
  a=KisBrokerAdapter(mode=BrokerMode.NO_SEND,account='12345678',whitelist={'0193W0','0193L0','0197X0'});o=a.prepare(req());self.assertEqual(o.status,BrokerOrderStatus.NO_SEND_VALIDATED);self.assertEqual(o.payload['PDNO'],'0197X0');self.assertEqual(a.network_send_calls,0);self.assertRaises(RuntimeError,a.submit,o);self.assertEqual(a.network_send_calls,0)
 def test_general_live_send_remains_blocked_without_network(self):
  a=KisBrokerAdapter(mode=BrokerMode.LIVE_SEND,account='x',whitelist={'0197X0'});o=a.prepare(req());self.assertRaises(RuntimeError,a.submit,o);self.assertEqual(a.network_send_calls,0)
 def test_all_products_sell_and_invalid(self):
  a=KisBrokerAdapter(mode=BrokerMode.NO_SEND,account='x',whitelist={'0193W0','0193L0','0197X0'});[self.assertEqual(a.prepare(req(x,'SELL')).payload['SLL_BUY_DVSN_CD'],'01') for x in a.whitelist];self.assertRaises(ValueError,a.prepare,req('BAD'));self.assertRaises(ValueError,a.prepare,req(qty=0))
 def test_fill_durable_model_and_s3_attribution(self):
  a=KisBrokerAdapter(mode=BrokerMode.NO_SEND,account='x',whitelist={'0197X0'});o=a.prepare(req());s=InMemoryBrokerStore();s.save_order(o);f=lambda tid,q,inst:BrokerFill.build(broker_order_id=o.broker_order_id,order_request_id=o.order_request_id,strategy_instance_id=inst,execution_stock_code='0197X0',side='BUY',fill_quantity=q,fill_price=100,gross_amount=q*100,fee=0,tax=0,other_cost=0,filled_at=datetime.now(),broker_trade_id=tid,raw_broker_detail={});s.record_fill(f('a',3,'S3_3'));s.record_fill(f('b',7,'S3_3'));self.assertEqual(s.orders[o.client_order_key].status,BrokerOrderStatus.FILLED);self.assertFalse(s.record_fill(f('b',7,'S3_3'))[1]);self.assertEqual(f('c',1,'S3_3').strategy_instance_id,'S3_3');self.assertEqual(f('c',1,'S3_5').strategy_instance_id,'S3_5')
 def test_no_network_import(self):
  for p in (ROOT/'src'/'broker').glob('*.py'):
   m=[a.name for n in ast.walk(ast.parse(p.read_text())) if isinstance(n,ast.Import) for a in n.names]+[n.module or '' for n in ast.walk(ast.parse(p.read_text())) if isinstance(n,ast.ImportFrom)]
   self.assertTrue(all(not any(x in z.lower() for x in ('requests','http','kis_client','order_service','collector')) for z in m))

 def test_unknown_restart_recovers_by_lookup_without_resend(self):
  adapter=KisBrokerAdapter(mode=BrokerMode.NO_SEND,account='x',whitelist={'0197X0'});store=InMemoryBrokerStore();order,_=store.save_order(adapter.prepare(req()))
  store.mark_unknown(order.client_order_key)  # durable UNKNOWN_BROKER_STATE before simulated crash
  submit_before=adapter.network_send_calls;lookup_calls=[]
  def lookup(client_key):
   lookup_calls.append(client_key)
   return BrokerOrder(**{**order.__dict__,'status':BrokerOrderStatus.ACCEPTED,'broker_order_number':'stub-1'})
  recovered=store.recover(order.client_order_key,lookup)
  self.assertEqual(recovered.status,BrokerOrderStatus.ACCEPTED);self.assertEqual(len(lookup_calls),1);self.assertEqual(adapter.network_send_calls,submit_before)

 def test_partial_fill_restart_recovery_is_append_only_and_idempotent(self):
  adapter=KisBrokerAdapter(mode=BrokerMode.NO_SEND,account='x',whitelist={'0197X0'});store=InMemoryBrokerStore();order,_=store.save_order(adapter.prepare(req(qty=100)))
  make=lambda trade,qty:BrokerFill.build(broker_order_id=order.broker_order_id,order_request_id=order.order_request_id,strategy_instance_id='S3_3',execution_stock_code='0197X0',side='BUY',fill_quantity=qty,fill_price=100,gross_amount=qty*100,fee=0,tax=0,other_cost=0,filled_at=datetime.now(),broker_trade_id=trade,raw_broker_detail={'stub':True})
  fill_a=make('A',30);store.record_fill(fill_a);before=len(store.fills)
  # Simulated restart: recovery receives old A and newly observed B.
  store.record_fill(make('A',30));store.record_fill(make('B',70));self.assertEqual(len(store.fills),before+1)
  self.assertEqual(sum(x.fill_quantity for x in store.fills.values() if x.broker_order_id==order.broker_order_id),100);self.assertEqual(store.orders[order.client_order_key].status,BrokerOrderStatus.FILLED)
  again=len(store.fills);store.record_fill(make('A',30));store.record_fill(make('B',70));self.assertEqual(len(store.fills),again);self.assertEqual(adapter.network_send_calls,0);self.assertTrue(all(x.strategy_instance_id=='S3_3' for x in store.fills.values()))
if __name__=='__main__':unittest.main()
