from __future__ import annotations
import json
from uuid import NAMESPACE_URL,uuid5
from src.broker.contracts import BrokerOrder,BrokerOrderStatus

class PostgresMinuteMaActualSubmitStore:
    def __init__(self,connection_factory):self.connection_factory=connection_factory
    def discover_ready_request_keys(self):
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("""SELECT r.idempotency_key FROM live_order_request r
              JOIN minute_ma_live_order_link l USING(order_request_id)
              JOIN minute_ma_live_intent i USING(intent_id)
              WHERE r.status='READY_FOR_BROKER' AND i.lifecycle_status='READY_FOR_BROKER'
              ORDER BY r.created_at,r.idempotency_key""")
            return tuple(str(x[0]) for x in q.fetchall())
    def claim(self,*,request_key):
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("""SELECT r.order_request_id,r.strategy_instance_id,r.execution_stock_code,r.side,
              r.requested_quantity,r.execution_target_time,r.status,i.intent_key
              FROM live_order_request r JOIN minute_ma_live_order_link l USING(order_request_id)
              JOIN minute_ma_live_intent i USING(intent_id)
              WHERE r.idempotency_key=%s FOR UPDATE""",(request_key,));row=q.fetchone()
            if row is None or row[6]!='READY_FOR_BROKER':c.commit();return None
            broker_id=str(uuid5(NAMESPACE_URL,'minute-ma-broker-order|'+request_key))
            payload={'order_policy':'MINUTE_MA_KRX_MARKET','intent_key':str(row[7])}
            q.execute("""INSERT INTO live_broker_order(broker_order_id,order_request_id,strategy_instance_id,
              execution_stock_code,side,quantity,client_order_key,status,payload)
              VALUES(%s,%s,%s,%s,%s,%s,%s,'SUBMITTING',%s::jsonb)
              ON CONFLICT(order_request_id) DO NOTHING RETURNING broker_order_id""",
              (broker_id,row[0],row[1],row[2],row[3],row[4],request_key,json.dumps(payload)))
            if q.fetchone() is None:c.commit();return None
            q.execute("UPDATE live_order_request SET status='SUBMITTING' WHERE order_request_id=%s",(row[0],))
            q.execute("UPDATE minute_ma_live_intent SET lifecycle_status='SUBMITTING',updated_at=CURRENT_TIMESTAMP WHERE intent_key=%s",(row[7],))
            c.commit()
        return BrokerOrder(broker_id,str(row[0]),str(row[1]),str(row[2]),str(row[3]),int(row[4]),request_key,
                           BrokerOrderStatus.SUBMITTING,payload,created_at=row[5])
    def acknowledge(self,*,order,raw):
        number=str((raw.get('output') or {}).get('ODNO') or '').strip()
        if not number:raise ValueError('MINUTE_MA_ACK_ORDER_NUMBER_REQUIRED')
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("UPDATE live_broker_order SET status='ACCEPTED',broker_order_number=%s WHERE broker_order_id=%s AND status='SUBMITTING'",(number,order.broker_order_id))
            q.execute("UPDATE minute_ma_live_order_link SET broker_order_id=%s WHERE order_request_id=%s",(order.broker_order_id,order.order_request_id))
            q.execute("UPDATE live_order_request SET status='ACCEPTED' WHERE order_request_id=%s",(order.order_request_id,))
            q.execute("""UPDATE minute_ma_live_intent i SET lifecycle_status='ACCEPTED',updated_at=CURRENT_TIMESTAMP
              FROM minute_ma_live_order_link l WHERE l.order_request_id=%s AND i.intent_id=l.intent_id""",(order.order_request_id,));c.commit()
    def mark_unknown(self,*,order):
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("UPDATE live_broker_order SET status='UNKNOWN_BROKER_STATE' WHERE broker_order_id=%s",(order.broker_order_id,))
            q.execute("UPDATE live_order_request SET status='UNKNOWN_BROKER_STATE' WHERE order_request_id=%s",(order.order_request_id,))
            q.execute("""UPDATE minute_ma_live_intent i SET lifecycle_status='UNKNOWN_BROKER_STATE',updated_at=CURRENT_TIMESTAMP
              FROM minute_ma_live_order_link l WHERE l.order_request_id=%s AND i.intent_id=l.intent_id""",(order.order_request_id,));c.commit()
    def pending_broker_orders(self):
        from types import SimpleNamespace
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("""SELECT b.broker_order_id,b.broker_order_number,b.execution_stock_code,b.side,b.quantity,
              b.created_at::date,i.intent_id,i.intent_type,i.minute_live_trade_id,i.minute_path_id
              FROM live_broker_order b JOIN minute_ma_live_order_link l USING(broker_order_id)
              JOIN minute_ma_live_intent i USING(intent_id)
              WHERE b.status IN ('ACCEPTED','PARTIALLY_FILLED','UNKNOWN_BROKER_STATE') ORDER BY b.created_at""")
            return tuple(SimpleNamespace(broker_order_id=str(x[0]),broker_order_number=str(x[1] or ''),stock_code=str(x[2]),
              side=str(x[3]),quantity=int(x[4]),order_date=x[5],intent_id=str(x[6]),intent_type=str(x[7]),
              minute_live_trade_id=x[8],minute_path_id=int(x[9])) for x in q.fetchall())
