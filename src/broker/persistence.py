from .contracts import BrokerOrder,BrokerOrderStatus,BrokerFill
class InMemoryBrokerStore:
 def __init__(self):self.orders={};self.fills={};self.audits=[]
 def save_order(self,o):
  old=self.orders.get(o.client_order_key)
  if old:self.audits.append(('BROKER_ORDER_DUPLICATE_SUPPRESSED',o.order_request_id));return old,False
  self.orders[o.client_order_key]=o;self.audits.append(('BROKER_NO_SEND_VALIDATED',o.order_request_id));return o,True
 def record_fill(self,f):
  old=self.fills.get(f.idempotency_key)
  if old:self.audits.append(('FILL_DUPLICATE_SUPPRESSED',f.broker_order_id));return old,False
  self.fills[f.idempotency_key]=f;qty=sum(x.fill_quantity for x in self.fills.values() if x.broker_order_id==f.broker_order_id);o=next(x for x in self.orders.values() if x.broker_order_id==f.broker_order_id);status=BrokerOrderStatus.FILLED if qty>=o.quantity else BrokerOrderStatus.PARTIALLY_FILLED;self.orders[o.client_order_key]=BrokerOrder(**{**o.__dict__,'status':status});self.audits.append(('ORDER_FILLED' if status==BrokerOrderStatus.FILLED else 'PARTIAL_FILL',f.broker_order_id));return f,True
 def mark_unknown(self,key):o=self.orders[key];self.orders[key]=BrokerOrder(**{**o.__dict__,'status':BrokerOrderStatus.UNKNOWN_BROKER_STATE});self.audits.append(('BROKER_STATE_UNKNOWN',o.order_request_id))
 def recover(self,key,broker_lookup):
  o=self.orders[key]
  if o.status!=BrokerOrderStatus.UNKNOWN_BROKER_STATE:return o
  result=broker_lookup(o.client_order_key);self.audits.append(('BROKER_RECOVERED',o.order_request_id));return result
