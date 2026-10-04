"""Durable FIRST_RISE cancellation using the existing KIS cancel contract.

Cancellation ACK is not terminal proof. Only JRecovery's exact order history
and cumulative checkpoint can unblock SECOND. Never manufacture a fill.
"""
import logging
from hashlib import sha256
from psycopg.types.json import Jsonb
from src.flow_v3.live_broker import FlowBrokerReader
from src.flow_v3.live_contract import cancel_payload
from .j_execution import STRATEGY_ID
from .j_live_repository import JLiveRepository

LOGGER=logging.getLogger(__name__)


class _SellCancelInquiryClient:
    """Reuse pagination/identity matching without changing FLOW's BUY filter."""
    def __init__(self,client):self.client=client

    @property
    def last_response_headers(self):return self.client.last_response_headers

    def get(self,*,path,tr_id,params,extra_headers=None):
        if tr_id!='TTTC0084R' or path!='/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl':
            raise ValueError('FIRST_RISE_CANCEL_INQUIRY_CONTRACT')
        # Official KIS INQR_DVSN_2: 0 all, 1 SELL, 2 BUY.
        return self.client.get(path=path,tr_id=tr_id,
            params=dict(params,INQR_DVSN_2='1'),extra_headers=extra_headers)


class JCancelRuntime:
    def __init__(self,pool,client,account,*,session_open):
        self.pool,self.client,self.account=pool,client,account
        self.reader=FlowBrokerReader(_SellCancelInquiryClient(client),account)
        self.session_open=session_open

    def cycle(self,*,at):
        # Pre-POST recovery orders are retired even outside the exchange session.
        with self.pool.connection() as c,c.transaction(),c.cursor() as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
            q.execute('''SELECT DISTINCT s.stock_code FROM first_rise_j_market_signal s
                JOIN first_rise_j_activation a ON a.strategy_id=%s
                WHERE s.signal_sequence=2 AND s.exit_reason IS NULL AND s.business_date=%s
                AND s.entry_signal_time>=a.effective_from AND NOT EXISTS(SELECT 1 FROM first_rise_j_live_intent i
                    WHERE i.market_signal_id=s.market_signal_id AND i.side='BUY')''',(STRATEGY_ID,at.date()))
            stocks=[r[0] for r in q.fetchall()]
            for stock in stocks:JLiveRepository.cancel_unsubmitted_sells(q,stock=stock)
            q.execute('''SELECT o.broker_order_id,o.broker_order_number,o.execution_stock_code,o.quantity
                FROM live_broker_order o WHERE o.strategy_instance_id=%s AND o.side='SELL'
                AND o.execution_stock_code=ANY(%s) AND o.status IN ('ACCEPTED','PARTIALLY_FILLED')''',(STRATEGY_ID,stocks))
            orders=q.fetchall()
        if not self.session_open(at):return 0
        sent=0
        for broker,number,stock,qty in orders:
            try:
                if not number:continue
                with self.pool.connection() as c,c.cursor() as q:
                    q.execute('SELECT post_attempted_at FROM first_rise_j_cancel_request WHERE broker_order_id=%s',(broker,))
                    prior=q.fetchone()
                    if prior and prior[0] is not None:continue
                matches=self.reader.cancellable_order(number,stock)
                if len(matches)!=1:continue
                row=matches[0];remaining=int(row.get('psbl_qty') or 0)
                branch=str(row.get('ord_gno_brno') or '').strip()
                if not 0<remaining<=qty or not branch:continue
                contract=cancel_payload(number,branch,remaining)
                key=sha256(f'FIRST_RISE_CANCEL|{broker}'.encode()).hexdigest()
                # Commit attempt before network. Crash/UNKNOWN never rePOSTs.
                with self.pool.connection() as c,c.transaction(),c.cursor() as q:
                    q.execute("SELECT pg_advisory_xact_lock(hashtext('first_rise_j_capital_epoch'))")
                    q.execute('''SELECT status,broker_order_number FROM live_broker_order
                        WHERE broker_order_id=%s AND strategy_instance_id=%s FOR UPDATE''',(broker,STRATEGY_ID))
                    current=q.fetchone()
                    if current is None or current[0] not in ('ACCEPTED','PARTIALLY_FILLED') or current[1]!=number:continue
                    q.execute('''INSERT INTO first_rise_j_cancel_request(broker_order_id,cancel_key,original_order_number,
                        branch,cancellable_quantity,status) VALUES(%s,%s,%s,%s,%s,'READY')
                        ON CONFLICT(broker_order_id) DO NOTHING''',(broker,key,number,branch,remaining))
                    q.execute('''UPDATE first_rise_j_cancel_request SET status='UNKNOWN',post_attempted_at=%s,
                        branch=%s,cancellable_quantity=%s WHERE broker_order_id=%s AND post_attempted_at IS NULL
                        RETURNING cancel_key''',(at,branch,remaining,broker))
                    if q.fetchone() is None:continue
                body=dict(contract['body'],CANO=self.account.cano,ACNT_PRDT_CD=self.account.account_product_code)
                try:
                    raw=self.client.post_once(path=contract['endpoint'],tr_id=contract['tr_id'],payload=body,custtype=self.account.custtype)
                except Exception:raw={}
                status='ACK' if str(raw.get('rt_cd'))=='0' else 'REJECTED' if raw.get('rt_cd') is not None else 'UNKNOWN'
                evidence={k:raw.get(k) for k in ('rt_cd','msg_cd','output')}
                with self.pool.connection() as c,c.transaction(),c.cursor() as q:
                    q.execute('''UPDATE first_rise_j_cancel_request SET status=%s,response=%s
                        WHERE broker_order_id=%s AND status<>'CONFIRMED' ''',(status,Jsonb(evidence),broker))
                sent+=1
            except Exception:
                LOGGER.exception('FIRST_RISE_CANCEL_ERROR broker_order_id=%s stock_code=%s',broker,stock)
        return sent


class KRXExecutionSession:
    """Exchange-day query cached daily; ENTRY cutoff never governs existing EXIT."""
    def __init__(self,calendar):self.calendar=calendar;self.day=None;self.open=False
    def __call__(self,at):
        from datetime import time
        if self.day!=at.date():
            self.open=at.date() in self.calendar.open_dates(at.date(),at.date())
            self.day=at.date()
        return self.open and time(9)<=at.time()<time(15,30)
