"""Explicit operator registration. Never changes global SEND, PAPER or old capital."""
from decimal import Decimal
from psycopg.rows import dict_row
from .live_contract import execution_product


class LiveOperations:
    def __init__(self,pool): self.pool=pool

    def register(self,strategy_id,route,amount,approval_reference,now,*,entry_enabled=False):
        amount=Decimal(amount)
        if not amount.is_finite() or not 0<amount<Decimal('1e18') or not approval_reference.strip():
            raise ValueError('APPROVED_CAPITAL_AND_REFERENCE_REQUIRED')
        with self.pool.connection() as c,c.transaction(),c.cursor(row_factory=dict_row) as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('FLOW_V3_LIVE_CYCLE'))")
            q.execute('SELECT * FROM flow_v3_strategy_master WHERE strategy_id=%s',(strategy_id,))
            m=q.fetchone()
            if not m or m['is_enabled']!='Y': raise ValueError('STRATEGY_NOT_ELIGIBLE')
            code=execution_product(m['stock_code'],m['direction'],route)
            q.execute("""SELECT 1 FROM flow_v3_strategy_operation WHERE strategy_id=%s
                AND execution_route=%s AND effective_to IS NULL""",(strategy_id,route))
            if q.fetchone(): raise ValueError('ACTIVE_ROUTE_ALREADY_EXISTS')
            q.execute('SELECT COALESCE(max(capital_epoch_no),0)+1 AS epoch FROM flow_v3_strategy_operation WHERE strategy_id=%s',(strategy_id,))
            epoch=q.fetchone()['epoch']
            q.execute("""INSERT INTO flow_v3_strategy_operation(strategy_id,operation_status,allocated_amount,
                capital_epoch_no,effective_from,change_reason,changed_by,memo,execution_route,live_execution_code,
                live_approved,entry_enabled,entry_resume_at,approval_reference)
                VALUES(%s,'LIVE',%s,%s,%s,'USER_APPROVED_ROUTE','FLOW_ROUTE_ADMIN',
                  'Independent operation capital; global SEND unchanged',%s,%s,true,%s,%s,%s)
                RETURNING operation_id""",(strategy_id,amount,epoch,now,route,code,entry_enabled,now,approval_reference))
            op=q.fetchone()['operation_id']
            q.execute("""INSERT INTO flow_v3_live_preparation(operation_id,strategy_id,direction,stock_code,
                execution_code,approval_reference,initial_capital,current_capital,reference_price_kind)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'LIVE_QUOTE_REQUIRED')""",
                (op,strategy_id,m['direction'],m['stock_code'],code,approval_reference,amount,amount))
            q.execute("""INSERT INTO flow_v3_live_capital(operation_id,strategy_id,initial_capital,current_capital,
                activated_at,initialization_basis) VALUES(%s,%s,%s,%s,%s,'USER_APPROVED')""",
                (op,strategy_id,amount,amount,now))
            return op

    def set_entry(self,operation_id,enabled,now,*,end=False):
        with self.pool.connection() as c,c.transaction(),c.cursor(row_factory=dict_row) as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext('FLOW_V3_LIVE_CYCLE'))")
            q.execute('SELECT * FROM flow_v3_strategy_operation WHERE operation_id=%s FOR UPDATE',(operation_id,))
            op=q.fetchone()
            if not op or not op['live_approved'] or op['execution_route'] is None:
                raise ValueError('LIVE_OPERATION_NOT_FOUND')
            if op['effective_to'] is not None: raise ValueError('OPERATION_ALREADY_ENDED')
            if end and now<=op['effective_from']: raise ValueError('END_TIME_INVALID')
            if enabled and not end and op['entry_enabled']:
                return  # Repeated start must not move a live operation's replay boundary.
            q.execute("""UPDATE flow_v3_strategy_operation SET entry_enabled=%s,
                entry_resume_at=CASE WHEN %s THEN %s ELSE entry_resume_at END,
                effective_to=CASE WHEN %s THEN %s ELSE effective_to END
                WHERE operation_id=%s""",(enabled and not end,enabled and not end,now,end,now,operation_id))
            # Existing intents/orders, OPEN lots and their capital are not rewritten.

    def listing(self):
        with self.pool.connection() as c,c.transaction(),c.cursor(row_factory=dict_row) as q:
            q.execute('SET TRANSACTION READ ONLY')
            q.execute("""SELECT o.*,c.initial_capital,c.current_capital,c.realized_net,
                (SELECT count(*) FROM flow_v3_live_lot l WHERE l.operation_id=o.operation_id
                  AND l.bought_quantity>l.sold_quantity) AS open_lots
                FROM flow_v3_strategy_operation o JOIN flow_v3_live_capital c USING(operation_id)
                ORDER BY o.strategy_id,o.operation_id""")
            return q.fetchall()
