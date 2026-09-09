"""FLOW-only, dual-authorized, at-most-once broker submission boundary."""
from .live_contract import validate_mapping, request_payload, cancel_payload
from .live_broker import kst_now
from .send_authorization import environment_enabled, send_authorized


def validate_claim(row, cancel):
    key, payload, sid, stock, direction, code, side, quantity, number, remaining = row
    validate_mapping(sid, stock, direction, code)
    if side not in ('BUY', 'SELL') or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError('FLOW_SEND_QUANTITY_OR_SIDE_INVALID')
    if cancel:
        if side != 'BUY' or not isinstance(remaining, int) or not 0 < remaining <= quantity:
            raise ValueError('FLOW_CANCEL_QUANTITY_INVALID')
        expected = cancel_payload(number, payload.get('body', {}).get('KRX_FWDG_ORD_ORGNO'), remaining)
    else:
        expected = request_payload(code, side, quantity)
    if payload != expected:
        raise ValueError('FLOW_SEND_PAYLOAD_IDENTITY_MISMATCH')


class FlowTransport:
    def __init__(self, repository, client, account):
        self.repository, self.client, self.account = repository, client, account

    def _not_sent(self, key, cancel, reason):
        # Known pre-HTTP denial, never a fabricated broker rejection or retry.
        with self.repository.pool.connection() as c, c.transaction():
            if cancel:
                c.execute("""UPDATE flow_v3_live_entry_release SET status='UNKNOWN',post_attempt_count=0,
                    cancel_response_message=%s WHERE entry_intent_id=%s""", (reason, key))
            else:
                c.execute("""UPDATE flow_v3_live_order SET status='UNKNOWN',post_attempt_count=0,
                    last_error=%s WHERE broker_order_id=%s""", (reason, key))

    def run(self):
        if not environment_enabled():
            return 0
        sent = 0
        # Claim commits before HTTP. Crashes/timeouts are never retryable READY.
        for cancel in ([True]*16 + [False]*32):
            with self.repository.pool.connection() as c, c.transaction():
                if not send_authorized(c):
                    return sent
                if cancel:
                    row = c.execute("""SELECT r.entry_intent_id,r.cancel_payload,i.strategy_id,
                        e.stock_code,e.direction,i.execution_code,i.side,i.quantity,
                        o.broker_order_number,r.cancellable_quantity
                        FROM flow_v3_live_entry_release r JOIN flow_v3_live_order o USING(broker_order_id)
                        JOIN flow_v3_live_intent i ON i.intent_id=o.intent_id
                        JOIN flow_v3_runtime_entry_event e ON e.event_id=i.event_id
                        WHERE o.send_enabled AND r.status='CANCEL_READY_NO_SEND' AND r.post_attempt_count=0
                          AND r.history_observed_at>=localtimestamp-interval '30 seconds'
                        ORDER BY r.requested_at FOR UPDATE OF r SKIP LOCKED LIMIT 1""").fetchone()
                else:
                    row = c.execute("""SELECT o.broker_order_id,o.request_payload,i.strategy_id,
                        e.stock_code,e.direction,i.execution_code,i.side,i.quantity,NULL,NULL
                        FROM flow_v3_live_order o JOIN flow_v3_live_intent i USING(intent_id)
                        JOIN flow_v3_runtime_entry_event e ON e.event_id=i.event_id
                        JOIN flow_v3_live_capital capital ON capital.strategy_id=i.strategy_id
                        JOIN flow_v3_strategy_operation op ON op.operation_id=capital.operation_id
                        WHERE o.send_enabled AND o.status='READY_NO_SEND' AND o.post_attempt_count=0
                          AND op.operation_status='LIVE' AND op.effective_to IS NULL
                          AND i.signal_time::date=current_date AND i.execution_not_before<=localtimestamp
                          AND i.reference_observed_at>=localtimestamp-interval '30 seconds'
                          AND localtime<CASE WHEN i.side='BUY' OR i.exit_reason='SIGNAL_EOD'
                              THEN time '15:20' ELSE time '15:30' END
                        ORDER BY CASE i.side WHEN 'SELL' THEN 0 ELSE 1 END,o.created_at
                        FOR UPDATE OF o SKIP LOCKED LIMIT 1""").fetchone()
                if row is None:
                    continue
                try:
                    validate_claim(row, cancel)
                except (ValueError, TypeError, KeyError) as exc:
                    reason = 'PRE_POST_VALIDATION_DENIED:' + type(exc).__name__
                    if cancel:
                        c.execute("""UPDATE flow_v3_live_entry_release SET status='UNKNOWN',
                            cancel_response_message=%s WHERE entry_intent_id=%s""", (reason, row[0]))
                    else:
                        c.execute("""UPDATE flow_v3_live_order SET status='UNKNOWN',last_error=%s
                            WHERE broker_order_id=%s""", (reason, row[0]))
                    continue
                if cancel:
                    c.execute("""UPDATE flow_v3_live_entry_release SET status='UNKNOWN',post_attempt_count=1
                        WHERE entry_intent_id=%s""", (row[0],))
                else:
                    c.execute("""UPDATE flow_v3_live_order SET status='SUBMITTING',post_attempt_count=1
                        WHERE broker_order_id=%s""", (row[0],))
            key, payload = row[:2]
            body = dict(payload['body'], CANO=self.account.cano,
                        ACNT_PRDT_CD=self.account.account_product_code)
            # Fresh DB read AFTER durable claim, immediately before the HTTP call.
            # Read errors fail closed with the claimed order unretried.
            with self.repository.pool.connection() as c:
                allowed = send_authorized(c)
            if not allowed:
                self._not_sent(key, cancel, 'FLOW_AUTHORIZATION_REVOKED_BEFORE_POST')
                return sent
            try:
                response = self.client.post_once(path=payload['endpoint'], tr_id=payload['tr_id'],
                                                 payload=body, custtype='P')
            except Exception:
                response = {}  # UNKNOWN: never fabricate REJECTED or resend.
            if cancel:
                self.repository.cancel_response(key, response, kst_now())
            else:
                self.repository.record_response(key, response, kst_now())
            sent += 1
        return sent
