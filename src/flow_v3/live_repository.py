"""FLOW-only transaction boundaries. Never writes PAPER, RAW or broker SEND queues."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .live_contract import (WHITELIST, CUTOFF, EOD_EXECUTION, validate_mapping,
                            order_quantity, request_payload, normal_exit, cumulative_delta,cancel_payload)


def identity(value):
    return uuid5(NAMESPACE_URL, 'FLOW_V3_LIVE|' + value)


class LiveRepository:
    def __init__(self, pool):
        self.pool = pool

    def activate(self, now):
        """Idempotent operation promotion; PAPER master enablement is untouched."""
        with self.pool.connection() as c, c.transaction(), c.cursor(row_factory=dict_row) as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('FLOW_V3_LIVE_CYCLE'))")
            q.execute('SELECT * FROM flow_v3_strategy_master WHERE strategy_id=ANY(%s)', (list(WHITELIST),))
            masters = q.fetchall()
            if len(masters) != 15:
                raise ValueError('FLOW_LIVE_MASTER_COUNT')
            q.execute("""SELECT max(trade_date) AS day FROM raw_stock_daily WHERE trading_venue='KRX'
                AND collect_cycle='DAILY' AND data_source='KIS' AND trade_date<%s""", (now.date(),))
            day = q.fetchone()['day']
            for m in masters:
                sid = m['strategy_id']
                validate_mapping(sid, m['stock_code'], m['direction'], m['execution_code'])
                q.execute('SELECT 1 FROM flow_v3_live_capital WHERE strategy_id=%s', (sid,))
                if q.fetchone():
                    continue  # No reinitialization of previously activated capital.
                q.execute("""SELECT close_price FROM raw_stock_daily WHERE stock_code=%s AND trade_date=%s
                    AND trading_venue='KRX' AND collect_cycle='DAILY' AND data_source='KIS'
                    AND close_price>0 ORDER BY collected_at DESC LIMIT 1""", (m['execution_code'], day))
                price = q.fetchone()
                if not price:
                    raise ValueError('FLOW_LIVE_PREVIOUS_CLOSE_MISSING:' + m['execution_code'])
                close = price['close_price']; capital = close*Decimal('1.5')
                q.execute("""UPDATE flow_v3_strategy_operation SET effective_to=%s WHERE strategy_id=%s
                    AND effective_to IS NULL""", (now,sid))
                q.execute("""INSERT INTO flow_v3_strategy_operation
                    (strategy_id,operation_status,allocated_amount,capital_epoch_no,effective_from,change_reason,changed_by,memo)
                    VALUES(%s,'LIVE',%s,1,%s,'FLOW_V3_USER_APPROVED_15','FLOW_V3_LIVE_ACTIVATION',
                    'Operation LIVE, physical broker SEND disabled; PAPER tracking independent') RETURNING operation_id""",
                    (sid,capital,now))
                operation_id = q.fetchone()['operation_id']
                q.execute("""INSERT INTO flow_v3_live_capital
                    (strategy_id,operation_id,initial_price_date,initial_close,initial_capital,current_capital,activated_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)""", (sid,operation_id,day,close,capital,capital,now))

    def cycle(self, now, quotes):
        """Existing fills/costs settle before new entries. No network inside this lock."""
        result = dict(entries=0, exits=0, settlements=0, requests=0, post=0)
        with self.pool.connection() as c, c.transaction(), c.cursor(row_factory=dict_row) as q:
            q.execute("SET LOCAL lock_timeout='3s'")
            q.execute("SET LOCAL statement_timeout='15s'")
            q.execute("SELECT pg_try_advisory_xact_lock(hashtext('FLOW_V3_LIVE_CYCLE')) AS acquired")
            if not q.fetchone()['acquired']:
                return dict(busy=True, post=0)
            result['settlements'] = self._settle(q)
            self._refresh_quotes(q, quotes, now)
            result['exits'] = self._exit_signals(q, now)
            self._release_exits(q)
            result['entries'] = self._entries(q, now)
            result['requests'] = self._requests(q, now)
            q.execute("""UPDATE flow_v3_live_intent i SET paper_trade_id=e.paper_trade_id
                FROM flow_v3_runtime_entry_event e WHERE i.event_id=e.event_id
                AND i.paper_trade_id IS NULL AND e.paper_trade_id IS NOT NULL""")
            q.execute("""UPDATE flow_v3_live_trade t SET paper_trade_id=i.paper_trade_id
                FROM flow_v3_live_intent i WHERE i.side='BUY' AND i.live_trade_id=t.live_trade_id
                AND t.paper_trade_id IS NULL AND i.paper_trade_id IS NOT NULL""")
            q.execute("""INSERT INTO flow_v3_live_worker_status(worker_code,heartbeat_at,cycle_result)
                VALUES('FLOW_V3_NO_SEND',now(),%s) ON CONFLICT(worker_code) DO UPDATE SET
                heartbeat_at=now(),cycle_result=EXCLUDED.cycle_result,last_error=NULL""", (Jsonb(result),))
        return result

    @staticmethod
    def _refresh_quotes(q, quotes, now):
        for code,(price,observed_at) in quotes.items():
            if code not in ('0193T0','0197X0') or price<=0 or not timedelta(0)<=now-observed_at<=timedelta(seconds=30):
                continue
            q.execute("""UPDATE flow_v3_live_capital c SET reference_price=%s,reference_observed_at=%s,
                next_quantity=greatest(0,floor(c.current_capital/%s))::bigint,last_error=NULL,updated_at=now()
                FROM flow_v3_live_preparation p WHERE c.strategy_id=p.strategy_id AND p.execution_code=%s""",
                (price,observed_at,price,code))

    @staticmethod
    def _entries(q, now):
        q.execute("""SELECT e.* FROM flow_v3_runtime_entry_event e
            JOIN flow_v3_live_capital c USING(strategy_id)
            JOIN flow_v3_strategy_operation o ON o.operation_id=c.operation_id
            WHERE e.entry_signal_time>=c.activated_at AND e.created_at>=c.activated_at
              AND o.operation_status='LIVE' AND o.effective_to IS NULL
              AND NOT EXISTS(SELECT 1 FROM flow_v3_live_intent i
                WHERE i.strategy_id=e.strategy_id AND i.entry_event_key=e.entry_event_key AND i.side='BUY')
            ORDER BY e.entry_signal_time,e.strategy_id,e.event_id LIMIT 1000""")
        events = q.fetchall(); count = 0
        for e in events:
            validate_mapping(e['strategy_id'],e['stock_code'],e['direction'],e['execution_code'])
            signal = e['entry_signal_time']; reason = None
            if signal.time()>CUTOFF:
                reason='ENTRY_AFTER_EOD_CUTOFF'
            elif signal.date()!=now.date() or now-signal>timedelta(minutes=3):
                reason='HISTORICAL_OR_STALE_ENTRY_NO_REPLAY'
            key=f"ENTRY|{e['strategy_id']}|{e['entry_event_key']}"
            q.execute("""INSERT INTO flow_v3_live_intent
                (intent_id,strategy_id,event_id,entry_event_key,paper_trade_id,side,lifecycle_key,
                 signal_time,execution_not_before,execution_code,status,reason)
                VALUES(%s,%s,%s,%s,%s,'BUY',%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (identity(key),e['strategy_id'],e['event_id'],e['entry_event_key'],e['paper_trade_id'],key,
                 signal,signal+timedelta(minutes=1),e['execution_code'],
                 'BLOCKED' if reason else 'WAITING_REFERENCE',reason))
            count += q.rowcount
        return count

    @staticmethod
    def _release_exits(q):
        # A zero-fill entry may fill while cancellation is being processed.
        # Attach the exit to that newly materialized lot, never lose its lifecycle.
        q.execute("""SELECT r.*,i.*,l.live_trade_id FROM flow_v3_live_entry_release r
            JOIN flow_v3_live_intent i ON i.intent_id=r.entry_intent_id
            JOIN flow_v3_live_lot l ON l.entry_intent_id=i.intent_id
            WHERE l.bought_quantity>l.sold_quantity AND NOT EXISTS
            (SELECT 1 FROM flow_v3_live_intent x WHERE x.side='SELL' AND x.live_trade_id=l.live_trade_id)""")
        for row in q.fetchall():
            signal=row['exit_signal_time'];reason=row['exit_reason']
            # i.exit_reason is NULL on BUY; obtain the release contract explicitly.
            q.execute('SELECT exit_reason FROM flow_v3_live_entry_release WHERE entry_intent_id=%s',(row['intent_id'],))
            reason=q.fetchone()['exit_reason']
            key=f"EXIT|{row['live_trade_id']}|{reason}|{signal.isoformat()}"
            q.execute("""INSERT INTO flow_v3_live_intent
                (intent_id,strategy_id,event_id,entry_event_key,paper_trade_id,live_trade_id,side,lifecycle_key,
                 signal_time,execution_not_before,exit_reason,execution_code,status)
                VALUES(%s,%s,%s,%s,%s,%s,'SELL',%s,%s,%s,%s,%s,'WAITING_REFERENCE') ON CONFLICT DO NOTHING""",
                (identity(key),row['strategy_id'],row['event_id'],row['entry_event_key'],row['paper_trade_id'],
                 row['live_trade_id'],key,signal,signal+timedelta(minutes=1),reason,row['execution_code']))

    def pending_cancellations(self):
        with self.pool.connection() as c,c.cursor(row_factory=dict_row) as q:
            q.execute("""SELECT r.*,o.broker_order_number,o.broker_order_date,i.execution_code,
                i.quantity,o.cumulative_quantity FROM flow_v3_live_entry_release r
                JOIN flow_v3_live_order o USING(broker_order_id) JOIN flow_v3_live_intent i USING(intent_id)
                WHERE r.status='WAIT_HISTORY' AND o.broker_order_number IS NOT NULL
                AND o.status IN ('ACK','PARTIAL','UNKNOWN')""")
            return q.fetchall()

    def prepare_cancel(self, entry_intent_id, *, number, branch, cancellable_quantity, observed_at):
        with self.pool.connection() as c,c.transaction(),c.cursor(row_factory=dict_row) as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('FLOW_V3_LIVE_CYCLE'))")
            q.execute("""SELECT r.*,o.broker_order_number,o.cumulative_quantity,i.quantity FROM flow_v3_live_entry_release r
                JOIN flow_v3_live_order o USING(broker_order_id) JOIN flow_v3_live_intent i USING(intent_id)
                WHERE r.entry_intent_id=%s FOR UPDATE OF r""",(entry_intent_id,))
            r=q.fetchone()
            if not r or r['status']!='WAIT_HISTORY':
                return
            if number!=r['broker_order_number'] or not 0<cancellable_quantity<=r['quantity']-r['cumulative_quantity'] or observed_at<r['requested_at']:
                raise ValueError('BROKER_CANCELLABLE_IDENTITY_OR_QUANTITY_INVALID')
            q.execute("""UPDATE flow_v3_live_entry_release SET status='CANCEL_READY_NO_SEND',cancellable_quantity=%s,
                cancel_payload=%s,history_observed_at=%s,updated_at=now() WHERE entry_intent_id=%s""",
                (cancellable_quantity,Jsonb(cancel_payload(number,branch,cancellable_quantity)),observed_at,entry_intent_id))

    def cancel_response(self, entry_intent_id, response, observed_at):
        with self.pool.connection() as c,c.transaction():
            # ACK is only cancellation acceptance, never terminal evidence.
            status='CANCEL_ACK' if str(response.get('rt_cd',''))=='0' else 'UNKNOWN'
            c.execute("""UPDATE flow_v3_live_entry_release SET status=%s,cancel_response_code=%s,
                cancel_response_message=%s,cancel_order_number=%s,cancel_responded_at=%s,updated_at=now()
                WHERE entry_intent_id=%s AND status='CANCEL_READY_NO_SEND'""",
                (status,str(response.get('msg_cd','')),str(response.get('msg1','')),
                 str((response.get('output') or {}).get('ODNO','')),observed_at,entry_intent_id))

    @staticmethod
    def _exit_signals(q, now):
        q.execute("""SELECT l.*,i.intent_id AS release_entry_id,o.broker_order_id,e.* FROM flow_v3_live_intent i
            JOIN flow_v3_live_order o USING(intent_id)
            LEFT JOIN flow_v3_live_lot l ON i.intent_id=l.entry_intent_id
            JOIN flow_v3_runtime_entry_event e ON e.event_id=i.event_id
            WHERE i.side='BUY' AND o.status IN ('ACK','PARTIAL','FILLED','UNKNOWN','CANCELLED')
              AND (l.live_trade_id IS NULL OR l.bought_quantity>l.sold_quantity) AND NOT EXISTS
             (SELECT 1 FROM flow_v3_live_entry_release r WHERE r.entry_intent_id=i.intent_id)
            ORDER BY i.signal_time,i.intent_id""")
        lots = q.fetchall(); count = 0
        for lot in lots:
            q.execute("""SELECT * FROM flow_v3_minute_state WHERE stock_code=%s AND bar_time>%s
                AND bar_time<=%s AND is_complete ORDER BY bar_time""",
                (lot['stock_code'],lot['last_source_bar'] or lot['entry_signal_time'],now-timedelta(minutes=1)))
            states=q.fetchall(); signal=None; reason=None
            for state in states:
                if normal_exit(lot,state):
                    signal=state['bar_time'];reason='NORMAL_EXIT';break
            if signal is None and lot['exit_policy_code']=='SIGNAL_EOD':
                eod=datetime.combine(lot['entry_signal_time'].date(),EOD_EXECUTION)
                if now>=eod:
                    # Do not race a delayed PAPER state builder at 15:19.
                    q.execute('SELECT max(last_bar_time) AS highwater FROM flow_v3_runtime_cursor WHERE stock_code=%s', (lot['stock_code'],))
                    highwater=q.fetchone()['highwater']
                    if highwater is None or highwater<datetime.combine(eod.date(),CUTOFF):
                        continue
                    signal=datetime.combine(lot['entry_signal_time'].date(),CUTOFF)
                    reason='SIGNAL_EOD'
            if signal is not None:
                q.execute("""INSERT INTO flow_v3_live_entry_release
                    (entry_intent_id,broker_order_id,exit_signal_time,exit_reason,requested_at,status)
                    VALUES(%s,%s,%s,%s,%s,'WAIT_HISTORY') ON CONFLICT DO NOTHING""",
                    (lot['release_entry_id'],lot['broker_order_id'],signal,reason,now))
                if lot['live_trade_id'] is None:
                    continue  # Zero-fill order is cancelled, never a fictitious LIVE lot.
                key=f"EXIT|{lot['live_trade_id']}|{reason}|{signal.isoformat()}"
                q.execute("""INSERT INTO flow_v3_live_intent
                    (intent_id,strategy_id,event_id,entry_event_key,paper_trade_id,live_trade_id,side,lifecycle_key,
                     signal_time,execution_not_before,exit_reason,execution_code,status)
                    VALUES(%s,%s,%s,%s,%s,%s,'SELL',%s,%s,%s,%s,%s,'WAITING_REFERENCE') ON CONFLICT DO NOTHING""",
                    (identity(key),lot['strategy_id'],lot['event_id'],lot['entry_event_key'],lot['paper_trade_id'],
                     lot['live_trade_id'],key,signal,signal+timedelta(minutes=1),reason,lot['execution_code']))
                count+=q.rowcount
                q.execute("UPDATE flow_v3_live_lot SET exposure_status='EXIT_PENDING' WHERE live_trade_id=%s",(lot['live_trade_id'],))
            if states and lot['live_trade_id'] is not None:
                q.execute('UPDATE flow_v3_live_lot SET last_source_bar=%s WHERE live_trade_id=%s',
                          (states[-1]['bar_time'],lot['live_trade_id']))
        return count

    def observe(self, broker_order_id, *, order_number, order_date, stock_code, side,
                requested_quantity, filled_quantity, filled_amount, status, observed_at,
                response_code=None, response_message=None, actual_fill_time=None,remaining_quantity=None):
        """Consume authoritative broker cumulative facts; observer time is not fill time.

        This method cannot submit, invent an ACK, or match UNKNOWN by quantity.
        An order must already have a durably known broker order number.
        """
        if status not in ('ACK','PARTIAL','FILLED','REJECTED','UNKNOWN','CANCELLED'):
            raise ValueError('BROKER_STATUS_INVALID')
        with self.pool.connection() as c,c.transaction(),c.cursor(row_factory=dict_row) as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('FLOW_V3_LIVE_CYCLE'))")
            q.execute("""SELECT o.*,i.strategy_id,i.event_id,i.entry_event_key,i.live_trade_id,i.side,
                i.execution_code,i.quantity,i.signal_time,i.paper_trade_id,c.operation_id
                FROM flow_v3_live_order o JOIN flow_v3_live_intent i USING(intent_id)
                JOIN flow_v3_live_capital c USING(strategy_id) WHERE o.broker_order_id=%s FOR UPDATE OF o""",(broker_order_id,))
            o=q.fetchone()
            if not o or not o['broker_order_number'] or (o['broker_order_number'],o['broker_order_date'])!=(order_number,order_date):
                raise ValueError('BROKER_ORDER_IDENTITY_NOT_DURABLY_LINKED')
            if (o['execution_code'],o['side'],o['quantity'])!=(stock_code,side,requested_quantity):
                raise ValueError('BROKER_ORDER_IDENTITY_MISMATCH')
            if observed_at<o['signal_time']+timedelta(minutes=1):
                raise ValueError('BROKER_OBSERVATION_BEFORE_EXECUTION')
            dq,da=cumulative_delta(o['cumulative_quantity'],o['cumulative_amount'],filled_quantity,filled_amount,o['quantity'])
            if actual_fill_time is not None and actual_fill_time.date()!=order_date:
                raise ValueError('BROKER_FILL_DATE_MISMATCH')
            if status=='FILLED' and filled_quantity!=requested_quantity:
                raise ValueError('BROKER_FALSE_FILLED')
            if status=='CANCELLED' and remaining_quantity!=0:
                raise ValueError('BROKER_CANCEL_NOT_FINAL')
            tid=o['live_trade_id']
            version=o['checkpoint_version']
            if dq:
                version+=1
                if side=='BUY' and tid is None:
                    q.execute("""INSERT INTO flow_v3_live_trade
                        (strategy_id,paper_trade_id,operation_id,entry_signal_time,entry_order_ref,broker_source,broker_detail)
                        VALUES(%s,%s,%s,%s,%s,'KIS_CUMULATIVE_HISTORY',%s) RETURNING live_trade_id""",
                        (o['strategy_id'],o['paper_trade_id'],o['operation_id'],o['signal_time'],order_number,
                         Jsonb(dict(entry_event_key=o['entry_event_key'],fill_time_source='UNKNOWN_UNLESS_BROKER_REPORTED'))))
                    tid=q.fetchone()['live_trade_id']
                    q.execute("INSERT INTO flow_v3_live_lot(live_trade_id,entry_intent_id,strategy_id,entry_event_key) VALUES(%s,%s,%s,%s)",
                        (tid,o['intent_id'],o['strategy_id'],o['entry_event_key']))
                    q.execute('UPDATE flow_v3_live_intent SET live_trade_id=%s WHERE intent_id=%s',(tid,o['intent_id']))
                if tid is None:
                    raise ValueError('SELL_LOT_MISSING')
                q.execute('SELECT * FROM flow_v3_live_lot WHERE live_trade_id=%s FOR UPDATE',(tid,))
                lot=q.fetchone()
                if side=='SELL' and lot['sold_quantity']+dq>lot['bought_quantity']:
                    raise ValueError('LIVE_LOT_OVERSELL')
                if side=='BUY':
                    q.execute("UPDATE flow_v3_live_lot SET bought_quantity=bought_quantity+%s,buy_amount=buy_amount+%s WHERE live_trade_id=%s",(dq,da,tid))
                    q.execute("""UPDATE flow_v3_live_trade SET entry_quantity=%s,entry_fill_avg_price=%s,
                        entry_fill_time=COALESCE(%s,entry_fill_time),updated_at=CURRENT_TIMESTAMP WHERE live_trade_id=%s""",
                        (filled_quantity,Decimal(filled_amount)/filled_quantity,actual_fill_time,tid))
                else:
                    sold=lot['sold_quantity']+dq; amount=lot['sell_amount']+da
                    closed=sold==lot['bought_quantity']
                    q.execute("""UPDATE flow_v3_live_lot SET sold_quantity=%s,sell_amount=%s,exposure_status=%s
                        WHERE live_trade_id=%s""",(sold,amount,'FILLED_COST_PENDING' if closed else 'EXIT_PENDING',tid))
                    q.execute("""UPDATE flow_v3_live_trade SET exit_quantity=%s,exit_fill_avg_price=%s,
                        exit_fill_time=COALESCE(%s,exit_fill_time),exit_signal_time=%s,exit_order_ref=%s,
                        trade_status=%s,updated_at=CURRENT_TIMESTAMP WHERE live_trade_id=%s""",
                        (sold,amount/sold,actual_fill_time,o['signal_time'],order_number,'CLOSED' if closed else 'OPEN',tid))
                q.execute("""INSERT INTO flow_v3_live_fill_checkpoint
                    (broker_order_id,checkpoint_version,live_trade_id,broker_trade_date,observed_at,actual_fill_time,
                     side,delta_quantity,delta_amount,cumulative_quantity,cumulative_amount)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (broker_order_id,version,tid,order_date,observed_at,actual_fill_time,side,dq,da,filled_quantity,filled_amount))
                q.execute("""INSERT INTO flow_v3_live_checkpoint_allocation
                    (broker_order_id,checkpoint_version,live_trade_id,stock_code,side,delta_quantity,delta_amount,broker_event_time,broker_trade_date)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (broker_order_id,version,tid,stock_code,side,dq,da,actual_fill_time,order_date))
            # Terminal status cannot be undone by an older, equal checkpoint.
            if not dq and o['status'] in ('FILLED','CANCELLED','REJECTED') and status!=o['status']:
                return dict(delta=0,duplicate=True)
            q.execute("""UPDATE flow_v3_live_order SET status=%s,cumulative_quantity=%s,cumulative_amount=%s,
                checkpoint_version=%s,last_observed_at=%s,response_code=%s,response_message=%s,updated_at=now()
                WHERE broker_order_id=%s""",(status,filled_quantity,filled_amount,version,observed_at,response_code,response_message,broker_order_id))
            q.execute('UPDATE flow_v3_live_intent SET status=%s,updated_at=now() WHERE intent_id=%s',(status,o['intent_id']))
            if side=='BUY' and status in ('FILLED','CANCELLED','REJECTED'):
                q.execute("""UPDATE flow_v3_live_entry_release SET status='CONFIRMED',final_quantity=%s,
                    history_observed_at=%s,updated_at=now() WHERE entry_intent_id=%s AND requested_at<=%s
                    AND (cancel_responded_at IS NULL OR cancel_responded_at<=%s)""",
                    (filled_quantity,observed_at,o['intent_id'],observed_at,observed_at))
            return dict(delta=dq,live_trade_id=tid,duplicate=dq==0)

    def orders_to_poll(self):
        with self.pool.connection() as c,c.cursor(row_factory=dict_row) as q:
            q.execute("""SELECT o.*,i.execution_code,i.side,i.quantity FROM flow_v3_live_order o
                JOIN flow_v3_live_intent i USING(intent_id) WHERE o.broker_order_number IS NOT NULL
                AND (o.status IN ('SUBMITTING','ACK','PARTIAL','UNKNOWN') OR EXISTS
                  (SELECT 1 FROM flow_v3_live_entry_release r WHERE r.broker_order_id=o.broker_order_id
                   AND r.status<>'CONFIRMED')) ORDER BY o.created_at LIMIT 100""")
            return q.fetchall()

    def record_response(self, broker_order_id, response, observed_at):
        """Durable response hook for the separately authorized submit boundary.

        No heuristic ACK, no resend, no secret-bearing raw response persistence.
        This No-SEND worker never calls it with a fabricated response.
        """
        explicit = str(response.get('rt_cd', ''))
        output = response.get('output') or {}
        number = str(output.get('ODNO') or output.get('odno') or '').strip()
        status = 'ACK' if explicit=='0' and number else 'REJECTED' if explicit and explicit!='0' else 'UNKNOWN'
        with self.pool.connection() as c,c.transaction():
            c.execute("SELECT pg_advisory_xact_lock(hashtext('FLOW_V3_LIVE_CYCLE'))")
            old=c.execute('SELECT status,intent_id,broker_order_number FROM flow_v3_live_order WHERE broker_order_id=%s FOR UPDATE',(broker_order_id,)).fetchone()
            if old is None:
                raise ValueError('FLOW_ORDER_NOT_FOUND')
            if old[0] not in ('READY_NO_SEND','SUBMITTING','UNKNOWN'):
                return old[0]
            if old[2] and number and old[2]!=number:
                raise ValueError('BROKER_RESPONSE_ORDER_NUMBER_CONFLICT')
            c.execute("""UPDATE flow_v3_live_order SET status=%s,broker_order_number=%s,broker_order_date=%s,
                response_code=%s,response_message=%s,responded_at=%s,updated_at=now() WHERE broker_order_id=%s""",
                (status,number or old[2],observed_at.date() if number else None,
                 str(response.get('msg_cd') or ''),str(response.get('msg1') or ''),observed_at,broker_order_id))
            c.execute('UPDATE flow_v3_live_intent SET status=%s,updated_at=now() WHERE intent_id=%s',(status,old[1]))
        return status

    def record_error(self, reason):
        with self.pool.connection() as c:
            c.execute("""INSERT INTO flow_v3_live_worker_status(worker_code,heartbeat_at,cycle_result,last_error)
                VALUES('FLOW_V3_NO_SEND',now(),'{}',%s) ON CONFLICT(worker_code) DO UPDATE
                SET heartbeat_at=now(),last_error=EXCLUDED.last_error""",(reason,))

    @staticmethod
    def _requests(q, now):
        q.execute("""SELECT i.*,c.current_capital,c.reference_price AS quote,c.reference_observed_at AS quote_time
            FROM flow_v3_live_intent i JOIN flow_v3_live_capital c USING(strategy_id)
            WHERE i.status='WAITING_REFERENCE' AND i.execution_not_before<=%s
            ORDER BY i.signal_time,CASE i.side WHEN 'SELL' THEN 0 ELSE 1 END,i.intent_id FOR UPDATE OF i,c""", (now,))
        rows=q.fetchall(); count=0
        for i in rows:
            deadline=time(15,20) if i['side']=='BUY' or i['exit_reason']=='SIGNAL_EOD' else time(15,30)
            if i['signal_time'].date()!=now.date() or now.time()>=deadline:
                q.execute("UPDATE flow_v3_live_intent SET status='BLOCKED',reason='EXECUTION_WINDOW_CLOSED' WHERE intent_id=%s",(i['intent_id'],))
                if i['side']=='SELL':
                    q.execute("UPDATE flow_v3_live_lot SET exposure_status='EOD_EXIT_UNFILLED' WHERE live_trade_id=%s",(i['live_trade_id'],))
                continue
            if i['quote'] is None or i['quote_time'] is None or not timedelta(0)<=now-i['quote_time']<=timedelta(seconds=30):
                continue
            if i['side']=='BUY':
                qty=order_quantity(i['current_capital'],i['quote'])
            else:
                # Do not sell while an entry can still fill: first resolve its
                # terminal state. No cancellation or synthetic fill is invented.
                q.execute("""SELECT l.bought_quantity-l.sold_quantity AS remaining,o.status,r.status AS release_status,
                    r.final_quantity,l.bought_quantity
                    FROM flow_v3_live_lot l JOIN flow_v3_live_order o ON o.intent_id=l.entry_intent_id
                    LEFT JOIN flow_v3_live_entry_release r ON r.entry_intent_id=l.entry_intent_id
                    WHERE l.live_trade_id=%s""",(i['live_trade_id'],))
                entry=q.fetchone()
                if entry['release_status']!='CONFIRMED' or entry['final_quantity']!=entry['bought_quantity']:
                    q.execute("UPDATE flow_v3_live_intent SET reason='ENTRY_CANCEL_FINAL_HISTORY_PENDING' WHERE intent_id=%s",(i['intent_id'],))
                    continue
                qty=int(entry['remaining'])
            if qty<=0:
                q.execute("UPDATE flow_v3_live_intent SET status='BLOCKED',reason='QUANTITY_ZERO' WHERE intent_id=%s",(i['intent_id'],))
                continue
            payload=request_payload(i['execution_code'],i['side'],qty)
            q.execute("""UPDATE flow_v3_live_intent SET quantity=%s,reference_price=%s,reference_observed_at=%s,
                capital_at_entry=%s,status='READY_NO_SEND',reason='PHYSICAL_SEND_DISABLED',updated_at=now() WHERE intent_id=%s""",
                (qty,i['quote'],i['quote_time'],i['current_capital'],i['intent_id']))
            q.execute("""INSERT INTO flow_v3_live_order(broker_order_id,intent_id,request_payload,status)
                VALUES(%s,%s,%s,'READY_NO_SEND') ON CONFLICT(intent_id) DO NOTHING""",
                (identity('ORDER|'+str(i['intent_id'])),i['intent_id'],Jsonb(payload)))
            count+=q.rowcount
        return count

    @staticmethod
    def _settle(q):
        q.execute("""SELECT l.*,t.entry_quantity FROM flow_v3_live_lot l JOIN flow_v3_live_trade t USING(live_trade_id)
            WHERE l.exposure_status='FILLED_COST_PENDING' AND NOT EXISTS
                (SELECT 1 FROM flow_v3_live_settlement s WHERE s.live_trade_id=l.live_trade_id)
            ORDER BY (SELECT max(observed_at) FROM flow_v3_live_fill_checkpoint f WHERE f.live_trade_id=l.live_trade_id),l.live_trade_id""")
        lots=q.fetchall();count=0
        for l in lots:
            q.execute("""WITH expected AS (SELECT broker_trade_date,side,sum(delta_amount) amount
                FROM flow_v3_live_fill_checkpoint WHERE live_trade_id=%s GROUP BY 1,2)
                SELECT e.*,s.status,a.fill_notional,a.buy_fee,a.sell_fee,a.sell_tax,a.other_cost
                FROM expected e JOIN flow_v3_live_preparation p ON p.strategy_id=%s
                LEFT JOIN broker_shared_cost_snapshot s ON s.trade_date=e.broker_trade_date AND s.execution_stock_code=p.execution_code
                LEFT JOIN broker_shared_cost_allocation a ON a.trade_date=e.broker_trade_date
                    AND a.execution_stock_code=p.execution_code AND a.family='FLOW' AND a.live_trade_id=%s AND a.side=e.side""",
                (l['live_trade_id'],l['strategy_id'],l['live_trade_id']))
            costs=q.fetchall()
            if not costs or any(x['status']!='FINALIZED_BY_STABLE_RECHECK' or x['fill_notional']!=x['amount'] for x in costs):
                continue
            fees=[sum((x[k] for x in costs),Decimal(0)) for k in ('buy_fee','sell_fee','sell_tax','other_cost')]
            gross=l['sell_amount']-l['buy_amount'];net=gross-sum(fees)
            q.execute('SELECT current_capital FROM flow_v3_live_capital WHERE strategy_id=%s FOR UPDATE',(l['strategy_id'],))
            before=q.fetchone()['current_capital'];after=before+net
            q.execute("""INSERT INTO flow_v3_live_settlement
                (live_trade_id,strategy_id,gross_pnl,buy_fee,sell_fee,sell_tax,other_cost,net_pnl,capital_before,capital_after)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (l['live_trade_id'],l['strategy_id'],gross,*fees,net,before,after))
            if q.rowcount!=1:
                continue
            q.execute("""UPDATE flow_v3_live_capital SET realized_net=realized_net+%s,current_capital=current_capital+%s,
                version=version+1,updated_at=now() WHERE strategy_id=%s""",(net,net,l['strategy_id']))
            q.execute("""UPDATE flow_v3_live_trade SET buy_fee=%s,sell_fee=%s,sell_tax=%s,other_cost=%s,
                gross_realized_pnl=%s,net_realized_pnl=%s,net_return_pct=%s,updated_at=CURRENT_TIMESTAMP
                WHERE live_trade_id=%s""",(*fees,gross,net,net/l['buy_amount']*100,l['live_trade_id']))
            q.execute("UPDATE flow_v3_live_lot SET exposure_status='SETTLED' WHERE live_trade_id=%s",(l['live_trade_id'],))
            count+=1
        return count
