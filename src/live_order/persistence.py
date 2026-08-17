"""Append-only in-memory planning store; mirrors the proposed DB transaction contract."""
from __future__ import annotations
from .contracts import CapitalAccount,CapitalEvent,CapitalEventType,OrderRequest
class InMemoryOrderPlanningStore:
 def __init__(self): self.accounts={};self.ledger=[];self.requests={};self.audits=[]
 def audit(self,event,detail):self.audits.append((event,detail))
 def ensure_account(self,instance,initial):
  return self.accounts.setdefault(instance,CapitalAccount(instance,initial))
 def account(self,instance): return self.accounts[instance]
 def request_by_key(self,key): return self.requests.get(key)
 def reserve_and_create(self,request,amount):
  existing=self.requests.get(request.idempotency_key)
  if existing:return existing,False
  account=self.accounts[request.strategy_instance_id]
  if amount>account.available_capital: raise ValueError('insufficient capital')
  updated=CapitalAccount(account.strategy_instance_id,account.initial_capital,account.realized_net_pnl,account.reserved_amount+amount)
  # Atomic model: both mutations occur together only after all validation.
  self.requests[request.idempotency_key]=request;self.accounts[account.strategy_instance_id]=updated
  self.ledger.append(CapitalEvent(account.strategy_instance_id,CapitalEventType.RESERVE,-amount,updated.available_capital,'ORDER_RESERVE',request.source_intent_id,request.order_request_id))
  return request,True
 def release(self,request):
  account=self.accounts[request.strategy_instance_id];updated=CapitalAccount(account.strategy_instance_id,account.initial_capital,account.realized_net_pnl,max(0,account.reserved_amount-request.reserved_capital));self.accounts[account.strategy_instance_id]=updated;self.ledger.append(CapitalEvent(account.strategy_instance_id,CapitalEventType.RELEASE,request.reserved_capital,updated.available_capital,'CANCELLED_BEFORE_SEND',request.source_intent_id,request.order_request_id));return updated
