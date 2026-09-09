"""FLOW submission adapter, behind closed code AND durable database gates.

The installed release cannot send: SEND_ENABLED=False, order.send_enabled has
CHECK(NOT send_enabled), and POST counters have CHECK(=0). A future authorization
release must explicitly open both gates. No environment variable bypass exists.
"""
from .live_contract import SEND_ENABLED
from .live_broker import kst_now


class FlowTransport:
    def __init__(self, repository, client, account):
        self.repository,self.client,self.account=repository,client,account

    def run(self):
        if not SEND_ENABLED:
            return 0
        sent=0
        # Claim commits before HTTP. A crash/timeout leaves an unresolved order,
        # never a retryable READY record. Neither loop selects UNKNOWN.
        for cancel in ([True]*16+[False]*32):
            with self.repository.pool.connection() as c,c.transaction():
                if cancel:
                    row=c.execute("""SELECT r.entry_intent_id,r.cancel_payload FROM flow_v3_live_entry_release r
                        JOIN flow_v3_live_order o USING(broker_order_id)
                        WHERE o.send_enabled AND r.status='CANCEL_READY_NO_SEND' AND r.post_attempt_count=0
                          AND r.history_observed_at>=localtimestamp-interval '30 seconds'
                        ORDER BY r.requested_at FOR UPDATE OF r SKIP LOCKED LIMIT 1""").fetchone()
                    if row:
                        c.execute("""UPDATE flow_v3_live_entry_release SET status='UNKNOWN',post_attempt_count=1
                            WHERE entry_intent_id=%s""",(row[0],))
                else:
                    row=c.execute("""SELECT o.broker_order_id,o.request_payload FROM flow_v3_live_order o
                        JOIN flow_v3_live_intent i USING(intent_id)
                        WHERE o.send_enabled AND o.status='READY_NO_SEND' AND o.post_attempt_count=0
                          AND i.signal_time::date=current_date AND i.execution_not_before<=localtimestamp
                          AND i.reference_observed_at>=localtimestamp-interval '30 seconds'
                          AND localtime<CASE WHEN i.side='BUY' OR i.exit_reason='SIGNAL_EOD'
                              THEN time '15:20' ELSE time '15:30' END
                        ORDER BY CASE i.side WHEN 'SELL' THEN 0 ELSE 1 END,o.created_at
                        FOR UPDATE OF o SKIP LOCKED LIMIT 1""").fetchone()
                    if row:
                        c.execute("UPDATE flow_v3_live_order SET status='SUBMITTING',post_attempt_count=1 WHERE broker_order_id=%s",(row[0],))
            if row is None:
                continue
            key,payload=row
            body=dict(payload['body'],CANO=self.account.cano,ACNT_PRDT_CD=self.account.account_product_code)
            try:
                response=self.client.post_once(path=payload['endpoint'],tr_id=payload['tr_id'],payload=body,custtype='P')
            except Exception:
                response={}  # UNKNOWN: never fabricate REJECTED or resend.
            if cancel:
                self.repository.cancel_response(key,response,kst_now())
            else:
                self.repository.record_response(key,response,kst_now())
            sent+=1
        return sent
