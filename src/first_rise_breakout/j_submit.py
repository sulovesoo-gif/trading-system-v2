"""FIRST_RISE binding for the existing durable submit orchestration.

No capital allocation account, new broker ledger, or send flag. A request is
claimed and committed before any network call. Uncertain claims are lookup-only.
"""
from uuid import NAMESPACE_URL, uuid5
from datetime import datetime
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from src.broker.contracts import BrokerOrder, BrokerOrderStatus
from src.collector.raw.kis_client import KISClientError
from .j_execution import STRATEGY_ID


class JSubmitStore:
    def __init__(self, connection_factory, *, config_provider=None, clock=None,session_open=None):
        self.connection_factory = connection_factory
        self.config_provider = config_provider
        self.clock = clock or (lambda: datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None))
        self.session_open=session_open or (lambda at:False)

    def discover_ready_request_keys(self):
        with self.connection_factory() as c, c.cursor() as q:
            q.execute('''SELECT r.idempotency_key FROM live_order_request r
                JOIN first_rise_j_live_intent i ON i.order_request_id=r.order_request_id
                WHERE r.strategy_instance_id=%s AND (r.status='READY_FOR_BROKER' OR
                    (r.status='SUBMITTING' AND EXISTS(SELECT 1 FROM live_broker_order o
                        WHERE o.order_request_id=r.order_request_id AND o.status='SUBMITTING'
                        AND NOT EXISTS(SELECT 1 FROM live_broker_order_audit a
                            WHERE a.broker_order_id=o.broker_order_id AND a.event_type='FIRST_RISE_POST_ATTEMPT'))))
                ORDER BY CASE WHEN r.side='SELL' THEN 0 ELSE 1 END,r.created_at,r.idempotency_key''', (STRATEGY_ID,))
            return tuple(row[0] for row in q.fetchall())

    def claim(self, *, request_key):
        if not self.session_open(self.clock()):return None
        with self.connection_factory() as c, c.transaction(), c.cursor() as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
            q.execute('''SELECT r.order_request_id,r.execution_stock_code,r.side,r.requested_quantity,
                r.status,r.execution_target_time,i.signal_time,i.trade_id,r.detail,s.exit_reason
                FROM live_order_request r
                JOIN first_rise_j_live_intent i ON i.order_request_id=r.order_request_id
                JOIN first_rise_j_market_signal s ON s.market_signal_id=i.market_signal_id
                JOIN first_rise_j_capital_binding b ON b.trade_id=i.trade_id
                WHERE r.idempotency_key=%s AND r.strategy_instance_id=%s
                  AND r.side=i.side AND r.execution_stock_code=b.stock_code
                  AND r.source_intent_id=i.intent_id AND r.source_decision_id=i.market_signal_id
                  AND r.detail->>'first_rise_trade_id'=i.trade_id::text
                  AND (r.side<>'BUY' OR COALESCE(s.entry_evidence->>'sequence_replay_only','false')<>'true')
                FOR UPDATE OF r''', (request_key,STRATEGY_ID))
            row=q.fetchone()
            if row is None or row[4] not in ('READY_FOR_BROKER','SUBMITTING'):
                return None
            q.execute('''SELECT o.broker_order_id,o.status,o.quantity,o.execution_stock_code,o.side,
                EXISTS(SELECT 1 FROM live_broker_order_audit a WHERE a.broker_order_id=o.broker_order_id
                    AND a.event_type='FIRST_RISE_POST_ATTEMPT') FROM live_broker_order o
                WHERE o.order_request_id=%s FOR UPDATE''',(row[0],))
            existing=q.fetchone()
            if existing and (existing[1]!='SUBMITTING' or existing[5]):return None
            if existing and existing[2:5]!=(row[3],row[1],row[2]):
                raise ValueError('FIRST_RISE_CLAIM_IDENTITY_MISMATCH')
            if row[2]=='BUY':
                q.execute('''SELECT 1 FROM live_order_request WHERE strategy_instance_id=%s
                    AND execution_stock_code=%s AND side='SELL'
                    AND status NOT IN ('FILLED','REJECTED','CANCELLED') LIMIT 1''',(STRATEGY_ID,row[1]))
                if q.fetchone():return None
                now=self.clock()
                config=self.config_provider() if self.config_provider else None
                if row[9] is not None or row[6].date()<now.date() or (config is not None and now.time()>=config.live_entry_cutoff):
                    q.execute("UPDATE live_order_request SET status='CANCELLED',reason='FIRST_RISE_STALE_UNSUBMITTED_ENTRY' WHERE order_request_id=%s",(row[0],))
                    if existing:q.execute("UPDATE live_broker_order SET status='CANCELLED' WHERE broker_order_id=%s",(existing[0],))
                    return None
                if (config is None or row[6].date()!=now.date() or row[6]>now
                        or not config.live_entry_start<=row[6].time()<config.live_entry_cutoff):
                    return None
                q.execute('SELECT effective_from FROM first_rise_j_activation WHERE strategy_id=%s', (STRATEGY_ID,))
                activation=q.fetchone()
                if activation is None or row[6]<activation[0]:
                    return None
            else:
                q.execute('''SELECT a.planned_quantity,c.buy_quantity-c.sell_quantity FROM first_rise_j_sell_allocation a
                    JOIN first_rise_j_live_cost c ON c.trade_id=a.trade_id
                    WHERE a.order_request_id=%s FOR UPDATE OF c''',(row[0],))
                owned=q.fetchall()
                if not owned or sum(p for p,_ in owned)!=row[3] or any(p>o for p,o in owned):
                    raise ValueError('FIRST_RISE_SELL_EXCEEDS_OWNED_QUANTITY')
            if row[3] is None or row[3]<=0:
                raise ValueError('FIRST_RISE_INVALID_REQUEST_QUANTITY')
            broker_id=uuid5(NAMESPACE_URL,'first-rise-broker|'+request_key)
            payload={'order_policy':'FIRST_RISE_KRX_MARKET','first_rise_trade_id':str(row[7])}
            if existing:
                broker_id=existing[0]
            else:
                q.execute('''INSERT INTO live_broker_order(broker_order_id,order_request_id,strategy_instance_id,
                    execution_stock_code,side,quantity,client_order_key,status,payload)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,'SUBMITTING',%s)
                    ON CONFLICT(order_request_id) DO NOTHING RETURNING broker_order_id''',
                    (broker_id,row[0],STRATEGY_ID,row[1],row[2],row[3],request_key,Jsonb(payload)))
                if q.fetchone() is None:return None
            q.execute("UPDATE live_order_request SET status='SUBMITTING',execution_target_time=%s WHERE order_request_id=%s", (self.clock(),row[0]))
        return BrokerOrder(str(broker_id),str(row[0]),STRATEGY_ID,row[1],row[2],row[3],request_key,
                           BrokerOrderStatus.SUBMITTING,payload,created_at=row[5])

    def mark_post_attempted(self, *, order):
        if not self.session_open(self.clock()):raise TimeoutError('FIRST_RISE_EXCHANGE_SESSION_CLOSED')
        with self.connection_factory() as c, c.transaction(), c.cursor() as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
            if order.side=='SELL':
                q.execute('''SELECT 1 FROM first_rise_j_live_intent i JOIN first_rise_j_market_signal next_signal
                    ON next_signal.prior_market_signal_id=i.market_signal_id
                    JOIN first_rise_j_activation a ON a.strategy_id=%s
                    WHERE i.order_request_id=%s AND next_signal.signal_sequence=2
                      AND next_signal.exit_reason IS NULL AND next_signal.entry_signal_time>=a.effective_from
                      AND next_signal.business_date=%s
                      AND COALESCE(next_signal.entry_evidence->>'sequence_replay_only','false')<>'true'
                      AND NOT EXISTS(SELECT 1 FROM first_rise_j_live_intent bi
                        WHERE bi.market_signal_id=next_signal.market_signal_id AND bi.side='BUY') LIMIT 1''',
                    (STRATEGY_ID,order.order_request_id,self.clock().date()))
                if q.fetchone():raise TimeoutError('FIRST_RISE_SECOND_SUPERSEDES_UNSENT_SELL')
            q.execute('''SELECT status FROM live_broker_order WHERE broker_order_id=%s
                AND order_request_id=%s AND strategy_instance_id=%s FOR UPDATE''',
                (order.broker_order_id,order.order_request_id,STRATEGY_ID))
            if q.fetchone()!=('SUBMITTING',):
                raise TimeoutError('FIRST_RISE_RESEND_FORBIDDEN')
            q.execute('''SELECT 1 FROM live_broker_order_audit WHERE broker_order_id=%s
                AND event_type='FIRST_RISE_POST_ATTEMPT' LIMIT 1''', (order.broker_order_id,))
            if q.fetchone():
                raise TimeoutError('FIRST_RISE_RESEND_FORBIDDEN')
            q.execute('''INSERT INTO live_broker_order_audit(event_type,broker_order_id,detail)
                VALUES('FIRST_RISE_POST_ATTEMPT',%s,'{}')''', (order.broker_order_id,))

    def _status(self, order, status, number=None):
        with self.connection_factory() as c, c.transaction(), c.cursor() as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
            q.execute('''UPDATE live_broker_order SET status=%s,broker_order_number=COALESCE(%s,broker_order_number)
                WHERE broker_order_id=%s AND order_request_id=%s AND strategy_instance_id=%s
                  AND status='SUBMITTING' RETURNING order_request_id''',
                (status,number,order.broker_order_id,order.order_request_id,STRATEGY_ID))
            updated=q.fetchone()
            if updated:
                q.execute('UPDATE live_order_request SET status=%s WHERE order_request_id=%s', (status,updated[0]))
                q.execute('''INSERT INTO live_broker_order_audit(event_type,broker_order_id,detail)
                    VALUES(%s,%s,'{}')''', ('FIRST_RISE_'+status,order.broker_order_id))

    def acknowledge(self, *, order, raw):
        number=str((raw.get('output') or {}).get('ODNO') or '').strip()
        self._status(order,'ACCEPTED' if number else 'UNKNOWN_BROKER_STATE',number or None)

    def mark_unknown(self, *, order):
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("SELECT 1 FROM live_broker_order_audit WHERE broker_order_id=%s AND event_type='FIRST_RISE_POST_ATTEMPT' LIMIT 1",(order.broker_order_id,))
            if q.fetchone() is None:return # proven pre-HTTP refusal is resumable
        self._status(order,'UNKNOWN_BROKER_STATE')

    def reject(self, *, order, raw):
        self._status(order,'REJECTED')

    def record_response(self,*,order,raw):
        detail={'rt_cd':str(raw.get('rt_cd') or ''),'msg_cd':str(raw.get('msg_cd') or ''),
                'msg1':str(raw.get('msg1') or ''),
                'order_number':str((raw.get('output') or {}).get('ODNO') or '')}
        with self.connection_factory() as c,c.transaction(),c.cursor() as q:
            q.execute("INSERT INTO live_broker_order_audit(event_type,broker_order_id,detail) VALUES('FIRST_RISE_POST_RESPONSE',%s,%s)",
                      (order.broker_order_id,Jsonb(detail)))
            if order.side=='SELL' and detail['rt_cd'] and detail['rt_cd']!='0':
                from .j_cost import retain_exit_recovery
                retain_exit_recovery(q,request_id=order.order_request_id,broker_id=order.broker_order_id,
                    at=self.clock(),source='KIS_POST_RESPONSE')


class JKISOrderTransport:
    """Thin KRX adapter. Used only by the separate execution process."""
    path='/uapi/domestic-stock/v1/trading/order-cash'

    def __init__(self, *, client, account, attempt_recorder):
        self.client,self.account,self.attempt_recorder=client,account,attempt_recorder

    def submit_once(self, order, *, profile=None):
        if (order.strategy_instance_id!=STRATEGY_ID or order.payload.get('order_policy')!='FIRST_RISE_KRX_MARKET'
                or order.side not in ('BUY','SELL') or order.quantity<=0):
            raise ValueError('FIRST_RISE_ORDER_OWNERSHIP_REQUIRED')
        self.attempt_recorder.mark_post_attempted(order=order)
        payload={'CANO':self.account.cano,'ACNT_PRDT_CD':self.account.account_product_code,
                 'PDNO':order.execution_stock_code,'ORD_DVSN':'01','ORD_QTY':str(order.quantity),
                 'ORD_UNPR':'0','EXCG_ID_DVSN_CD':'KRX'}
        if order.side=='SELL':
            payload['SLL_TYPE']='01'
        try:
            raw=self.client.post_once(path=self.path,tr_id='TTTC0012U' if order.side=='BUY' else 'TTTC0011U',
                payload=payload,custtype=self.account.custtype)
            self.attempt_recorder.record_response(order=order,raw=raw)
            return raw
        except KISClientError as error:
            raise TimeoutError('FIRST_RISE_KIS_SUBMIT_UNKNOWN') from error
