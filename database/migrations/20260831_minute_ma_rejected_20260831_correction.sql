-- One-time evidence-guarded correction for the 34 explicit KIS rejections on 2026-08-31.
-- No broker submit or replay is performed.
BEGIN;

DO $$
DECLARE
  v_count INTEGER;
  v_existing INTEGER;
BEGIN
  SELECT count(*) INTO v_existing
  FROM minute_ma_live_broker_rejection
  WHERE evidence_type='SYSTEMD_JOURNAL_CORRECTION';
  IF v_existing = 34 THEN
    RETURN;
  ELSIF v_existing <> 0 THEN
    RAISE EXCEPTION 'MINUTE_MA_20260831_REJECT_CORRECTION_PARTIAL_%',v_existing;
  END IF;
  SELECT count(*) INTO v_count
  FROM live_broker_order b
  JOIN live_order_request r USING(order_request_id)
  JOIN minute_ma_live_order_link l USING(order_request_id)
  JOIN minute_ma_live_intent i USING(intent_id)
  JOIN minute_ma_live_signal_event e
    ON e.minute_live_signal_event_id=i.minute_live_signal_event_id
  WHERE e.event_type='ENTRY'
    AND e.source_bar_time::date=DATE '2026-08-31'
    AND b.status='SUBMITTING'
    AND r.status='SUBMITTING'
    AND i.lifecycle_status='SUBMITTING'
    AND l.broker_order_id IS NULL
    AND NOT EXISTS (SELECT 1 FROM live_broker_fill f WHERE f.broker_order_id=b.broker_order_id);
  IF v_count <> 34 THEN
    RAISE EXCEPTION 'MINUTE_MA_20260831_REJECT_CORRECTION_EXPECTED_34_ACTUAL_%',v_count;
  END IF;
END $$;

INSERT INTO minute_ma_live_broker_submit_attempt(
  broker_order_id,order_request_id,intent_id,kis_tr_id,kis_endpoint,attempted_at)
SELECT b.broker_order_id,b.order_request_id,i.intent_id,
  CASE WHEN b.side='BUY' THEN 'TTTC0012U' ELSE 'TTTC0011U' END,
  '/uapi/domestic-stock/v1/trading/order-cash',
  CASE e.source_bar_time::time
    WHEN TIME '09:00' THEN TIMESTAMP '2026-08-31 09:02:08'
    WHEN TIME '09:05' THEN TIMESTAMP '2026-08-31 09:07:06'
    WHEN TIME '09:19' THEN TIMESTAMP '2026-08-31 09:21:07'
    WHEN TIME '09:20' THEN TIMESTAMP '2026-08-31 09:22:06'
    WHEN TIME '09:27' THEN TIMESTAMP '2026-08-31 09:29:07'
  END
FROM live_broker_order b
JOIN live_order_request r USING(order_request_id)
JOIN minute_ma_live_order_link l USING(order_request_id)
JOIN minute_ma_live_intent i USING(intent_id)
JOIN minute_ma_live_signal_event e
  ON e.minute_live_signal_event_id=i.minute_live_signal_event_id
WHERE e.event_type='ENTRY' AND e.source_bar_time::date=DATE '2026-08-31'
  AND b.status='SUBMITTING'
ON CONFLICT(broker_order_id) DO NOTHING;

INSERT INTO minute_ma_live_broker_rejection(
  broker_order_id,order_request_id,intent_id,response_code,response_message,
  kis_tr_id,kis_endpoint,rejected_at,response_payload,evidence_type)
SELECT b.broker_order_id,b.order_request_id,i.intent_id,NULL,
  'REJECTED_LEGACY_NO_RESPONSE_DETAIL',
  CASE WHEN b.side='BUY' THEN 'TTTC0012U' ELSE 'TTTC0011U' END,
  '/uapi/domestic-stock/v1/trading/order-cash',
  CASE e.source_bar_time::time
    WHEN TIME '09:00' THEN TIMESTAMP '2026-08-31 09:02:08'
    WHEN TIME '09:05' THEN TIMESTAMP '2026-08-31 09:07:06'
    WHEN TIME '09:19' THEN TIMESTAMP '2026-08-31 09:21:07'
    WHEN TIME '09:20' THEN TIMESTAMP '2026-08-31 09:22:06'
    WHEN TIME '09:27' THEN TIMESTAMP '2026-08-31 09:29:07'
  END,
  jsonb_build_object(
    'runtime_summary','submitted.REJECTED',
    'source_bar_time',e.source_bar_time,
    'detail_recoverable',false,
    'correction_contract','SYSTEMD_JOURNAL_EXPLICIT_REJECTION_NO_RAW_RESPONSE'),
  'SYSTEMD_JOURNAL_CORRECTION'
FROM live_broker_order b
JOIN live_order_request r USING(order_request_id)
JOIN minute_ma_live_order_link l USING(order_request_id)
JOIN minute_ma_live_intent i USING(intent_id)
JOIN minute_ma_live_signal_event e
  ON e.minute_live_signal_event_id=i.minute_live_signal_event_id
WHERE e.event_type='ENTRY' AND e.source_bar_time::date=DATE '2026-08-31'
  AND b.status='SUBMITTING'
ON CONFLICT(broker_order_id) DO NOTHING;

UPDATE live_broker_order b SET status='REJECTED'
FROM minute_ma_live_broker_rejection x
WHERE x.broker_order_id=b.broker_order_id
  AND x.evidence_type='SYSTEMD_JOURNAL_CORRECTION' AND b.status='SUBMITTING';

UPDATE live_order_request r SET status='REJECTED',reason='KIS_REJECTED'
FROM minute_ma_live_broker_rejection x
WHERE x.order_request_id=r.order_request_id
  AND x.evidence_type='SYSTEMD_JOURNAL_CORRECTION' AND r.status='SUBMITTING';

UPDATE minute_ma_live_intent i
SET lifecycle_status='REJECTED',block_reason='KIS_REJECTED',updated_at=CURRENT_TIMESTAMP
FROM minute_ma_live_broker_rejection x
WHERE x.intent_id=i.intent_id
  AND x.evidence_type='SYSTEMD_JOURNAL_CORRECTION'
  AND i.lifecycle_status='SUBMITTING';

INSERT INTO live_broker_order_audit(event_type,broker_order_id,detail)
SELECT 'MINUTE_MA_ORDER_REJECTED_CORRECTION',x.broker_order_id,
  jsonb_build_object('response_message',x.response_message,
    'evidence_type',x.evidence_type,'rejected_at',x.rejected_at)
FROM minute_ma_live_broker_rejection x
WHERE x.evidence_type='SYSTEMD_JOURNAL_CORRECTION'
  AND NOT EXISTS (
    SELECT 1 FROM live_broker_order_audit a
    WHERE a.broker_order_id=x.broker_order_id
      AND a.event_type='MINUTE_MA_ORDER_REJECTED_CORRECTION');

COMMIT;
