"""KIS protocol mapping only. NO_SEND never owns a transport."""
from __future__ import annotations
from .contracts import BrokerMode,BrokerOrder,BrokerOrderStatus,client_key
from src.live_order.contracts import OrderRequest
class KisBrokerAdapter:
 def __init__(self,*,mode:BrokerMode,account:str,whitelist:set[str],phase_7c_transport=None):self.mode,self.account,self.whitelist=mode,account,set(whitelist);self.phase_7c_transport=phase_7c_transport;self.network_send_calls=0
 def prepare(self,request:OrderRequest):
  if request.execution_stock_code not in self.whitelist:raise ValueError('invalid product')
  if not request.requested_quantity or request.requested_quantity<=0:raise ValueError('invalid quantity')
  payload={'CANO':self.account,'PDNO':request.execution_stock_code,'ORD_DVSN':'01','ORD_QTY':str(request.requested_quantity),'SLL_BUY_DVSN_CD':'02' if request.side=='BUY' else '01','client_order_key':client_key(request.order_request_id),'execution_target_time':request.execution_target_time.isoformat()}
  status=BrokerOrderStatus.NO_SEND_VALIDATED if self.mode==BrokerMode.NO_SEND else BrokerOrderStatus.PREPARED
  return BrokerOrder(request.order_request_id,request.order_request_id,request.strategy_instance_id,request.execution_stock_code,request.side,request.requested_quantity,payload['client_order_key'],status,payload)
 def submit(self,order):
  if self.mode==BrokerMode.NO_SEND:raise RuntimeError('NO_SEND: network submit forbidden')
  if self.mode==BrokerMode.PHASE_7C_SMOKE_SEND:
   if self.phase_7c_transport is None:raise RuntimeError('PHASE_7C_TRANSPORT_REQUIRED')
   self.network_send_calls+=1
   return self.phase_7c_transport.submit_once(order)
  self.network_send_calls+=1;raise RuntimeError('LIVE_SEND disabled in phase 7B')
 def parse_response(self,raw):return BrokerOrderStatus.ACCEPTED if raw.get('rt_cd')=='0' else BrokerOrderStatus.REJECTED
