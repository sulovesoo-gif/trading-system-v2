"""Durable, globally serialized cost allocation. No order submission API."""
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL,uuid5
from src.daily_ma_v03.broker_cost_allocation import BrokerCostSnapshot,BrokerCostStatus,BrokerCostTotals
from src.daily_ma_v03.broker_cost_finalization import StableCostRecheck,stable_recheck,next_krx_trading_date
from .shared_cost_allocation import OwnedCheckpoint,allocate_shared_costs


class SharedBrokerCostFinalizer:
    def __init__(self,*,connection_factory,cost_lookup,calendar):
        self.factory,self.lookup,self.calendar=connection_factory,cost_lookup,calendar

    def finalize_due(self,*,today):
        with self.factory() as c,c.cursor() as q:
            q.execute("""SELECT trade_date,execution_stock_code FROM daily_strategy_live_broker_cost_snapshot
                UNION SELECT trade_date,execution_stock_code FROM minute_ma_live_broker_cost_snapshot
                UNION SELECT broker_event_time::date,stock_code FROM flow_v3_live_checkpoint_allocation""")
            days=q.fetchall()
        final=0
        for day,stock in days:
            next_day=next_krx_trading_date(trade_date=day,calendar=self.calendar)
            if today<next_day: continue
            final+=self._finalize(day,stock,next_day)
        return dict(product_days=len(days),finalized=final)

    def _finalize(self,day,stock,next_day):
        with self.factory() as c,c.cursor() as q:
            q.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",(f'BROKER_SHARED_COST|{day}|{stock}',))
            q.execute("""SELECT buy_fee,sell_fee,sell_tax,other_cost,broker_snapshot_at,status,
                fingerprint,confirmation_count,last_confirmed_at FROM broker_shared_cost_snapshot
                WHERE trade_date=%s AND execution_stock_code=%s FOR UPDATE""",(day,stock))
            prior=q.fetchone()
            q.execute("""SELECT 'DAILY',t.live_trade_id,a.broker_order_id::text,a.checkpoint_version,
                    a.side,a.delta_quantity,a.delta_amount
                FROM daily_strategy_live_checkpoint_allocation a JOIN daily_strategy_live_trade t
                  ON t.ownership_id=a.ownership_id
                WHERE a.stock_code=%s AND a.broker_event_time::date=%s
                UNION ALL SELECT 'MINUTE',minute_live_trade_id,broker_order_id::text,checkpoint_version,
                    side,delta_quantity,delta_amount FROM minute_ma_live_checkpoint_allocation
                WHERE stock_code=%s AND broker_event_time::date=%s
                UNION ALL SELECT 'FLOW',live_trade_id,broker_order_id::text,checkpoint_version,
                    side,delta_quantity,delta_amount FROM flow_v3_live_checkpoint_allocation
                WHERE stock_code=%s AND broker_event_time::date=%s ORDER BY 1,2,3,4""",
                (stock,day,stock,day,stock,day))
            rows=q.fetchall()
            fingerprint=sha256(repr(rows).encode()).hexdigest()
            q.execute("""SELECT count(*) FROM daily_strategy_live_checkpoint_allocation a
                LEFT JOIN daily_strategy_live_trade t ON t.ownership_id=a.ownership_id
                WHERE a.stock_code=%s AND a.broker_event_time::date=%s AND t.live_trade_id IS NULL""",(stock,day))
            missing_owner=q.fetchone()[0]>0
            q.execute("""SELECT EXISTS(SELECT 1 FROM daily_strategy_live_checkpoint_allocation
                WHERE stock_code=%s AND broker_event_time IS NULL) OR
                EXISTS(SELECT 1 FROM minute_ma_live_checkpoint_allocation
                WHERE stock_code=%s AND broker_event_time IS NULL)""",(stock,stock))
            unattributed=q.fetchone()[0] or missing_owner
            if prior and prior[5]=='FINALIZED_BY_STABLE_RECHECK':
                if prior[6]!=fingerprint:
                    q.execute("""UPDATE broker_shared_cost_snapshot SET status='BROKER_COST_ATTRIBUTION_BLOCKED',
                       failure_reason='FINALIZED_FILL_SET_CHANGED' WHERE trade_date=%s AND execution_stock_code=%s""",(day,stock))
                    c.commit()
                return 0
            raw=self.lookup.lookup(trade_date=day,execution_stock_code=stock)
            observed=BrokerCostSnapshot(day,stock,raw.totals,raw.broker_snapshot_at,False,BrokerCostStatus.PENDING_BROKER_COST)
            stored=None if not prior else StableCostRecheck(
                BrokerCostSnapshot(day,stock,BrokerCostTotals(*prior[:4]),prior[4],False,BrokerCostStatus(prior[5])),
                prior[6],False,prior[7],prior[8])
            state=stable_recheck(stored=stored,observed=observed,fill_set_fingerprint=fingerprint,
                unattributed_activity=unattributed,next_trade_date=next_day,minimum_interval=timedelta(minutes=10))
            failure='BROKER_CHECKPOINT_DATE_OR_OWNER_MISSING' if unattributed else None
            status=BrokerCostStatus.BROKER_COST_ATTRIBUTION_BLOCKED if unattributed else state.snapshot.status
            allocations=()
            if state.snapshot.final and not unattributed:
                try:
                    status,allocations=allocate_shared_costs(snapshot=state.snapshot,
                        checkpoints=[OwnedCheckpoint(*r) for r in rows])
                except ValueError as exc:
                    status=BrokerCostStatus.BROKER_COST_ATTRIBUTION_BLOCKED
                    failure=str(exc)
            q.execute("""INSERT INTO broker_shared_cost_snapshot
                (trade_date,execution_stock_code,buy_fee,sell_fee,sell_tax,other_cost,broker_snapshot_at,
                 status,fingerprint,confirmation_count,last_confirmed_at,failure_reason)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(trade_date,execution_stock_code) DO UPDATE SET
                 buy_fee=EXCLUDED.buy_fee,sell_fee=EXCLUDED.sell_fee,sell_tax=EXCLUDED.sell_tax,
                 other_cost=EXCLUDED.other_cost,broker_snapshot_at=EXCLUDED.broker_snapshot_at,
                 status=EXCLUDED.status,fingerprint=EXCLUDED.fingerprint,
                 confirmation_count=EXCLUDED.confirmation_count,last_confirmed_at=EXCLUDED.last_confirmed_at,
                 failure_reason=EXCLUDED.failure_reason,updated_at=now()""",
                (day,stock,raw.totals.buy_fee,raw.totals.sell_fee,raw.totals.sell_tax,raw.totals.other_cost,
                 raw.broker_snapshot_at,status.value,fingerprint,state.confirmation_count,state.last_confirmed_at,failure))
            if status is not BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK:
                c.commit()
                return 0
            for (family,tid,side),a in allocations:
                q.execute("""INSERT INTO broker_shared_cost_allocation
                    (trade_date,execution_stock_code,family,live_trade_id,side,fill_notional,buy_fee,sell_fee,sell_tax,other_cost)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (day,stock,family,tid,side,a.fill_notional,a.buy_fee,a.sell_fee,a.sell_tax,a.other_cost))
            self._publish(q,day,stock,state,allocations)
            c.commit()
            return 1

    @staticmethod
    def _publish(q,day,stock,state,allocations):
        # Consumers receive exactly their globally allocated slice; no second rounding.
        for family,prefix,id_column,uuid_prefix in (
            ('DAILY','daily_strategy_live','live_trade_id','daily-ma-v042-cost'),
            ('MINUTE','minute_ma_live','minute_live_trade_id','minute-ma-cost')):
            own=[(key,a) for key,a in allocations if key[0]==family]
            if not own: continue
            snapshot_id=str(uuid5(NAMESPACE_URL,f'{uuid_prefix}|{day}|{stock}'))
            totals=[sum((getattr(a,field) for _,a in own),Decimal(0))
                    for field in ('buy_fee','sell_fee','sell_tax','other_cost')]
            q.execute(f"""INSERT INTO {prefix}_broker_cost_snapshot
                (broker_cost_snapshot_id,trade_date,execution_stock_code,broker_buy_fee,broker_sell_fee,
                 broker_sell_tax,broker_other_cost,broker_snapshot_at,finalization_status,finalized_at,
                 stable_confirmation_count,fill_set_fingerprint,last_stable_recheck_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'FINALIZED_BY_STABLE_RECHECK',%s,%s,%s,%s)
                ON CONFLICT(trade_date,execution_stock_code) DO UPDATE SET
                 broker_buy_fee=EXCLUDED.broker_buy_fee,broker_sell_fee=EXCLUDED.broker_sell_fee,
                 broker_sell_tax=EXCLUDED.broker_sell_tax,broker_other_cost=EXCLUDED.broker_other_cost,
                 broker_snapshot_at=EXCLUDED.broker_snapshot_at,finalization_status=EXCLUDED.finalization_status,
                 finalized_at=EXCLUDED.finalized_at,stable_confirmation_count=EXCLUDED.stable_confirmation_count,
                 fill_set_fingerprint=EXCLUDED.fill_set_fingerprint,last_stable_recheck_at=EXCLUDED.last_stable_recheck_at""",
                (snapshot_id,day,stock,*totals,state.snapshot.broker_snapshot_at,state.snapshot.broker_snapshot_at,
                 state.confirmation_count,state.fill_set_fingerprint,state.last_confirmed_at))
            for (_,tid,side),a in own:
                q.execute(f"""INSERT INTO {prefix}_broker_cost_allocation
                    (broker_cost_snapshot_id,{id_column},allocation_side,fill_notional,allocated_buy_fee,
                     allocated_sell_fee,allocated_sell_tax,allocated_other_cost,stable_allocation_key)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (snapshot_id,tid,side,a.fill_notional,a.buy_fee,a.sell_fee,a.sell_tax,a.other_cost,
                     f'SHARED|{family}|{tid}|{side}'))
