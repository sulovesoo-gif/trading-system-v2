from __future__ import annotations
import json
from decimal import Decimal,ROUND_FLOOR
from hashlib import sha256
from uuid import NAMESPACE_URL,uuid5

def digest(x):return sha256(x.encode()).hexdigest()

class PostgresMinuteMaLivePlanner:
    def __init__(self,connection_factory):self.connection_factory=connection_factory
    def _profile(self,q):
        q.execute("SELECT send_enabled FROM minute_ma_send_profile WHERE profile_code='MINUTE_MA_LIVE_SEND'")
        if q.fetchone()!=("Y",):raise PermissionError('MINUTE_MA_SEND_LOCKED')
    @staticmethod
    def _reconciled(q,stock):
        q.execute("SELECT status,unattributed_quantity FROM execution_reconciliation_audit WHERE stock_code=%s ORDER BY checked_at DESC,reconciliation_id DESC LIMIT 1",(stock,));r=q.fetchone()
        return r is not None and r[0] in ('PASS','HEALTHY') and int(r[1])==0
    def plan_entry(self,*,path,event,reference_price,available_cash,
                   underlying_entry_reference_price=None):
        policy_path_id=getattr(path,'minute_policy_path_id',None)
        key=digest(f'MINUTE_MA_V1|ENTRY|{policy_path_id or path.minute_path_id}|{event.signal_event_key}')
        intent_id=str(uuid5(NAMESPACE_URL,'minute-ma-intent|'+key));request_id=str(uuid5(NAMESPACE_URL,'minute-ma-request|'+key))
        with self.connection_factory() as c,c.cursor() as q:
            self._profile(q)
            if policy_path_id is not None:
                q.execute("""SELECT o.minute_policy_operation_id,o.capital_epoch_no,
                  cc.strategy_compound_capital FROM minute_ma_policy_operation o
                  JOIN minute_ma_policy_compound_capital cc
                    ON cc.minute_policy_path_id=o.minute_policy_path_id
                   AND cc.capital_epoch_no=o.capital_epoch_no
                  WHERE o.minute_policy_path_id=%s AND o.effective_to IS NULL
                    AND o.operation_status='LIVE' FOR UPDATE""",(policy_path_id,))
            else:
                q.execute("""SELECT o.operation_id,o.capital_epoch_no,cc.strategy_compound_capital FROM minute_ma_operation o
                  JOIN minute_ma_compound_capital cc ON cc.minute_path_id=o.minute_path_id AND cc.capital_epoch_no=o.capital_epoch_no
                  WHERE o.minute_path_id=%s AND o.effective_to IS NULL AND o.operation_status='LIVE' FOR UPDATE""",(path.minute_path_id,))
            op=q.fetchone()
            if op is None:return 'OPERATION_NOT_LIVE'
            if not self._reconciled(q,path.execution_code):return 'RECONCILIATION_REQUIRED'
            q.execute("SELECT lifecycle_status FROM minute_ma_live_intent WHERE intent_key=%s",(key,));old=q.fetchone()
            if old:return old[0]
            operation_id,epoch,capital=op;price=Decimal(reference_price)
            policy=getattr(path,'operation_policy',None)
            anchor=(None if underlying_entry_reference_price is None
                    else Decimal(underlying_entry_reference_price))
            threshold=None if policy is None or anchor is None else policy.threshold(anchor)
            stop_policy=None if policy is None else ('UNDERLYING_1PCT' if policy.direction=='SHORT' else 'UNDERLYING_5PCT')
            qty=int((Decimal(capital)/price).to_integral_value(rounding=ROUND_FLOOR));notional=price*qty
            if qty<=0 or notional>Decimal(available_cash):
                reason='ZERO_QUANTITY' if qty<=0 else 'INSUFFICIENT_AVAILABLE_CASH'
                q.execute("""INSERT INTO minute_ma_live_entry_skip(skip_id,minute_path_id,minute_paper_trade_id,
                  signal_event_key,capital_epoch_no,capital_at_signal,planned_quantity,planned_notional,skip_reason,
                  minute_policy_path_id,minute_policy_operation_id)
                  VALUES(%s,%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                  (str(uuid5(NAMESPACE_URL,'minute-ma-skip|'+key)),path.minute_path_id,event.signal_event_key,
                   epoch,capital,qty,notional,reason,policy_path_id,
                   operation_id if policy_path_id is not None else None));c.commit();return reason
            event_id=str(uuid5(NAMESPACE_URL,'minute-ma-live-event|'+event.signal_event_key+'|'+str(policy_path_id or path.minute_path_id)))
            q.execute("""INSERT INTO minute_ma_live_signal_event(minute_live_signal_event_id,minute_path_id,signal_event_key,event_type,
              source_bar_time,confirmed_at,source_snapshot,minute_policy_path_id,event_reason)
              VALUES(%s,%s,%s,'ENTRY',%s,%s,%s::jsonb,%s,'POLICY_ENTRY') ON CONFLICT DO NOTHING""",
              (event_id,path.minute_path_id,event.signal_event_key,event.source_bar_time,event.confirmed_at,json.dumps({'ma':event.ma_values,'previous_ma':event.previous_ma_values,'trend_passed':event.trend_passed}),policy_path_id))
            strategy=(f'MINUTE_MA_V1_POLICY:{policy_path_id}:EPOCH:{epoch}' if policy_path_id is not None
                      else f'MINUTE_MA_PATH:{path.minute_path_id}:EPOCH:{epoch}')
            request_key=digest('MINUTE_MA_V01|REQUEST|'+key+'|BUY')
            q.execute("""INSERT INTO minute_ma_live_intent(intent_id,intent_key,minute_path_id,minute_live_signal_event_id,
              intent_type,source_event_time,reference_price,requested_quantity,capital_at_signal,lifecycle_status,
              minute_policy_path_id,minute_policy_operation_id,underlying_entry_reference_price,stop_threshold_price,stop_policy)
              VALUES(%s,%s,%s,%s,'ENTRY',%s,%s,%s,%s,'READY_FOR_BROKER',%s,%s,%s,%s,%s)
              ON CONFLICT(intent_key) DO NOTHING""",
              (intent_id,key,path.minute_path_id,event_id,event.confirmed_at,price,qty,capital,
               policy_path_id,operation_id if policy_path_id is not None else None,anchor,threshold,stop_policy))
            q.execute("""INSERT INTO live_order_request(order_request_id,idempotency_key,strategy_instance_id,source_intent_id,
              source_decision_id,execution_stock_code,side,requested_notional,requested_quantity,reference_price,order_type,
              execution_target_time,strategy_capital_before,reserved_capital,safety_status,status,reason,detail)
              VALUES(%s,%s,%s,%s,%s,%s,'BUY',%s,%s,%s,'MARKET',%s,%s,%s,'PASS','READY_FOR_BROKER','MINUTE_MA_LIVE_SEND',%s::jsonb)
              ON CONFLICT(idempotency_key) DO NOTHING""",
              (request_id,request_key,strategy,intent_id,str(uuid5(NAMESPACE_URL,'minute-ma-decision|'+key)),path.execution_code,
               notional,qty,price,event.confirmed_at,capital,notional,json.dumps({
                   'minute_path_id':path.minute_path_id,'minute_policy_path_id':policy_path_id,
                   'operation_id':None if policy_path_id is not None else operation_id,
                   'minute_policy_operation_id':operation_id if policy_path_id is not None else None})))
            q.execute("INSERT INTO minute_ma_live_order_link(intent_id,order_request_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",(intent_id,request_id))
            q.execute("INSERT INTO minute_ma_live_capital_reservation(intent_id,reserved_amount,reservation_status) VALUES(%s,%s,'RESERVED') ON CONFLICT DO NOTHING",(intent_id,notional));c.commit();return 'READY_FOR_BROKER'
    def plan_exit(self,*,path,event,reference_price,exit_reason='NORMAL_EXIT'):
        policy_path_id=getattr(path,'minute_policy_path_id',None)
        event_id=str(uuid5(NAMESPACE_URL,'minute-ma-live-event|'+event.signal_event_key+'|'+str(policy_path_id or path.minute_path_id)))
        with self.connection_factory() as c,c.cursor() as q:
            self._profile(q)
            q.execute("""INSERT INTO minute_ma_live_signal_event(minute_live_signal_event_id,minute_path_id,signal_event_key,event_type,
              source_bar_time,confirmed_at,source_snapshot,minute_policy_path_id,event_reason)
              VALUES(%s,%s,%s,'EXIT',%s,%s,%s::jsonb,%s,%s) ON CONFLICT DO NOTHING""",
              (event_id,path.minute_path_id,event.signal_event_key,event.source_bar_time,event.confirmed_at,
               json.dumps({'ma':event.ma_values,'previous_ma':event.previous_ma_values,'trend_passed':event.trend_passed}),
               policy_path_id,exit_reason))
            q.execute("""SELECT t.minute_live_trade_id,t.ownership_id,t.capital_at_signal,t.capital_epoch_no,
              t.minute_policy_operation_id,
              COALESCE(lp.quantity,0) FROM minute_ma_live_trade t LEFT JOIN execution_logical_position lp
              ON lp.ownership_type='MINUTE_MA' AND lp.ownership_id=t.ownership_id AND lp.stock_code=%s
              WHERE t.minute_path_id=%s AND t.trade_status='OPEN'
                AND ((%s IS NULL AND t.minute_policy_path_id IS NULL) OR t.minute_policy_path_id=%s)
              ORDER BY t.minute_live_trade_id""",(path.execution_code,path.minute_path_id,
                policy_path_id,policy_path_id));trades=q.fetchall()
        statuses=[]
        for trade in trades:statuses.append(self._plan_one_exit(path=path,event=event,event_id=event_id,reference_price=reference_price,trade=trade,exit_reason=exit_reason))
        return statuses
    def plan_trade_exit(self,*,path,event,reference_price,minute_live_trade_id,exit_reason='STOP_EXIT'):
        """Plan exactly one ownership-scoped trade; used by per-trade STOP."""
        policy_path_id=getattr(path,'minute_policy_path_id',None)
        event_id=str(uuid5(NAMESPACE_URL,'minute-ma-live-event|'+event.signal_event_key+'|'+str(policy_path_id or path.minute_path_id)))
        with self.connection_factory() as c,c.cursor() as q:
            self._profile(q)
            q.execute("""INSERT INTO minute_ma_live_signal_event(minute_live_signal_event_id,minute_path_id,
              signal_event_key,event_type,source_bar_time,confirmed_at,source_snapshot,minute_policy_path_id,event_reason)
              VALUES(%s,%s,%s,'EXIT',%s,%s,%s::jsonb,%s,%s) ON CONFLICT DO NOTHING""",
              (event_id,path.minute_path_id,event.signal_event_key,event.source_bar_time,event.confirmed_at,
               json.dumps({'target_minute_live_trade_id':minute_live_trade_id}),
               getattr(path,'minute_policy_path_id',None),exit_reason))
            q.execute("""SELECT t.minute_live_trade_id,t.ownership_id,t.capital_at_signal,t.capital_epoch_no,
              t.minute_policy_operation_id,
              COALESCE(lp.quantity,0) FROM minute_ma_live_trade t LEFT JOIN execution_logical_position lp
              ON lp.ownership_type='MINUTE_MA' AND lp.ownership_id=t.ownership_id AND lp.stock_code=%s
              WHERE t.minute_live_trade_id=%s AND t.minute_path_id=%s AND t.trade_status='OPEN'""",
              (path.execution_code,minute_live_trade_id,path.minute_path_id));trade=q.fetchone()
        if trade is None:return 'OPEN_LIVE_TRADE_REQUIRED'
        return self._plan_one_exit(path=path,event=event,event_id=event_id,reference_price=reference_price,
                                   trade=trade,exit_reason=exit_reason)
    def _plan_one_exit(self,*,path,event,event_id,reference_price,trade,exit_reason):
        trade_id,ownership,capital,epoch,policy_operation_id,qty=trade
        if int(qty)<=0:return 'OWNERSHIP_REQUIRED'
        key=digest(f'MINUTE_MA_V01|EXIT|{trade_id}|{exit_reason}|{event.confirmed_at.isoformat()}')
        intent_id=str(uuid5(NAMESPACE_URL,'minute-ma-intent|'+key));request_id=str(uuid5(NAMESPACE_URL,'minute-ma-request|'+key));price=Decimal(reference_price)
        with self.connection_factory() as c,c.cursor() as q:
            if not self._reconciled(q,path.execution_code):return 'RECONCILIATION_REQUIRED'
            q.execute("SELECT lifecycle_status FROM minute_ma_live_intent WHERE intent_key=%s",(key,));old=q.fetchone()
            if old:return old[0]
            q.execute("""INSERT INTO minute_ma_live_intent(intent_id,intent_key,minute_path_id,minute_live_signal_event_id,minute_live_trade_id,intent_type,
              source_event_time,reference_price,requested_quantity,capital_at_signal,lifecycle_status,
              minute_policy_path_id,minute_policy_operation_id,target_minute_live_trade_id,exit_reason)
              VALUES(%s,%s,%s,%s,%s,'EXIT',%s,%s,%s,%s,'READY_FOR_BROKER',%s,%s,%s,%s)""",
              (intent_id,key,path.minute_path_id,event_id,trade_id,event.confirmed_at,price,qty,capital,
                getattr(path,'minute_policy_path_id',None),policy_operation_id,trade_id,exit_reason))
            request_key=digest('MINUTE_MA_V01|REQUEST|'+key+'|SELL')
            q.execute("""INSERT INTO live_order_request(order_request_id,idempotency_key,strategy_instance_id,source_intent_id,
              source_decision_id,execution_stock_code,side,requested_notional,requested_quantity,reference_price,order_type,
              execution_target_time,strategy_capital_before,reserved_capital,safety_status,status,reason,detail)
              VALUES(%s,%s,%s,%s,%s,%s,'SELL',%s,%s,%s,'MARKET',%s,%s,0,'PASS','READY_FOR_BROKER',%s,%s::jsonb)""",
              (request_id,request_key,
               (f'MINUTE_MA_V1_POLICY:{getattr(path,"minute_policy_path_id",None)}:LIVE_TRADE:{trade_id}'
                if getattr(path,'minute_policy_path_id',None) is not None
                else f'MINUTE_MA_PATH:{path.minute_path_id}:LIVE_TRADE:{trade_id}'),intent_id,
               str(uuid5(NAMESPACE_URL,'minute-ma-decision|'+key)),path.execution_code,price*qty,qty,price,event.confirmed_at,
               capital,exit_reason,json.dumps({'minute_path_id':path.minute_path_id,'minute_live_trade_id':trade_id,'ownership_id':ownership,'exit_reason':exit_reason})))
            q.execute("INSERT INTO minute_ma_live_order_link(intent_id,order_request_id) VALUES(%s,%s)",(intent_id,request_id));c.commit();return 'READY_FOR_BROKER'
