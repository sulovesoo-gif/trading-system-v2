from __future__ import annotations
from types import SimpleNamespace
from datetime import datetime
from src.daily_ma_v03.kis_order_history import UnknownResolution

class MinuteMaCheckpointPoller:
    def __init__(self,*,repository,history_lookup,checkpoint_store):self.repository,self.history_lookup,self.checkpoint_store=repository,history_lookup,checkpoint_store
    def poll(self):
        advanced=unknown=0
        for row in self.repository.pending_broker_orders():
            records=self.history_lookup.orders_for_day(order_date=row.order_date,stock_code=row.stock_code,side=row.side,order_number=row.broker_order_number)
            if not row.broker_order_number:
                resolution,match=self.history_lookup.resolve(records=records,expected_quantity=row.quantity)
                if resolution is UnknownResolution.UNRESOLVED:unknown+=1;continue
                if resolution is UnknownResolution.REJECTED:
                    self._reject(row,match.order_number);continue
                self._recover_ack(row,match.order_number);row.broker_order_number=match.order_number;records=(match,)
            for item in records:
                if item.order_number!=row.broker_order_number:continue
                values=dict(row.__dict__);values['broker_order_number']=item.order_number
                wrapped=SimpleNamespace(**values)
                event_time=datetime.strptime(item.order_date+item.order_time,'%Y%m%d%H%M%S') if item.order_date and item.order_time else datetime.now()
                result=self.checkpoint_store.apply(order=wrapped,cumulative_quantity=item.total_filled_quantity,
                  cumulative_amount=item.total_filled_amount,average_price=item.average_fill_price,event_time=event_time)
                advanced+=int(result.status=='ADVANCED')
        return {'advanced':advanced,'unknown':unknown}
    def _recover_ack(self,row,number):
        with self.repository.connection_factory() as c,c.cursor() as q:
            q.execute("UPDATE live_broker_order SET broker_order_number=%s,status='ACCEPTED' WHERE broker_order_id=%s",(number,row.broker_order_id))
            q.execute("UPDATE minute_ma_live_order_link SET broker_order_id=%s WHERE intent_id=%s",(row.broker_order_id,row.intent_id))
            q.execute("UPDATE live_order_request SET status='ACCEPTED' WHERE order_request_id=(SELECT order_request_id FROM live_broker_order WHERE broker_order_id=%s)",(row.broker_order_id,))
            q.execute("UPDATE minute_ma_live_intent SET lifecycle_status='ACCEPTED',updated_at=CURRENT_TIMESTAMP WHERE intent_id=%s",(row.intent_id,));c.commit()
    def _reject(self,row,number):
        with self.repository.connection_factory() as c,c.cursor() as q:
            q.execute("UPDATE live_broker_order SET broker_order_number=%s,status='REJECTED' WHERE broker_order_id=%s",(number,row.broker_order_id))
            q.execute("UPDATE minute_ma_live_intent SET lifecycle_status='REJECTED' WHERE intent_id=%s",(row.intent_id,));c.commit()
