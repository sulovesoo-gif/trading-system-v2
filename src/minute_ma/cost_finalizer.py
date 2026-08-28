from __future__ import annotations
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL,uuid5
from src.daily_ma_v03.broker_cost_allocation import (BrokerCostSnapshot,BrokerCostStatus,BrokerCostTotals,
  CostAllocationTarget,allocate_final_costs)
from src.daily_ma_v03.broker_cost_finalization import StableCostRecheck,next_krx_trading_date,stable_recheck
from .capital import PostgresMinuteMaCapitalStore,SettlementAmounts

class MinuteMaCostFinalizer:
    def __init__(self,*,connection_factory,cost_lookup,calendar,clock):
        self.connection_factory,self.cost_lookup,self.calendar,self.clock=connection_factory,cost_lookup,calendar,clock
        self.capital=PostgresMinuteMaCapitalStore(connection_factory)
    def finalize_due(self,*,today):
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("SELECT trade_date,execution_stock_code FROM minute_ma_live_broker_cost_snapshot WHERE finalization_status='PENDING_BROKER_COST' ORDER BY trade_date,execution_stock_code");rows=q.fetchall()
        finalized=settled=0
        for trade_date,stock in rows:
            next_day=next_krx_trading_date(trade_date=trade_date,calendar=self.calendar)
            if today<next_day:continue
            raw=self.cost_lookup.lookup(trade_date=trade_date,execution_stock_code=stock)
            with self.connection_factory() as c,c.cursor() as q:
                q.execute("""SELECT broker_order_id,checkpoint_version,minute_live_trade_id,side,delta_amount
                  FROM minute_ma_live_checkpoint_allocation WHERE stock_code=%s AND broker_event_time::date=%s
                  ORDER BY broker_order_id,checkpoint_version""",(stock,trade_date));fill_rows=q.fetchall()
                q.execute("""SELECT count(*) FROM daily_strategy_live_checkpoint_allocation
                  WHERE stock_code=%s AND broker_event_time::date=%s""",(stock,trade_date));foreign_count=int(q.fetchone()[0])
                q.execute("""SELECT broker_buy_fee,broker_sell_fee,broker_sell_tax,broker_other_cost,broker_snapshot_at,
                  finalization_status,fill_set_fingerprint,stable_confirmation_count,last_stable_recheck_at
                  FROM minute_ma_live_broker_cost_snapshot WHERE trade_date=%s AND execution_stock_code=%s FOR UPDATE""",(trade_date,stock));prior=q.fetchone()
            fingerprint=sha256(repr(fill_rows).encode()).hexdigest();observed=BrokerCostSnapshot(trade_date,stock,raw.totals,raw.broker_snapshot_at,False,BrokerCostStatus.PENDING_BROKER_COST)
            stored=None
            if prior:
                snapshot=BrokerCostSnapshot(trade_date,stock,BrokerCostTotals(*prior[:4]),prior[4],prior[5]==BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK.value,BrokerCostStatus(prior[5]))
                stored=StableCostRecheck(snapshot,prior[6] or '',foreign_count>0,int(prior[7]),prior[8])
            state=stable_recheck(stored=stored,observed=observed,fill_set_fingerprint=fingerprint,
              unattributed_activity=foreign_count>0,next_trade_date=next_day,minimum_interval=timedelta(minutes=10))
            snapshot_id=str(uuid5(NAMESPACE_URL,f'minute-ma-cost|{trade_date}|{stock}'))
            status=BrokerCostStatus.BROKER_COST_ATTRIBUTION_BLOCKED if foreign_count else state.snapshot.status
            with self.connection_factory() as c,c.cursor() as q:
                q.execute("""UPDATE minute_ma_live_broker_cost_snapshot SET broker_buy_fee=%s,broker_sell_fee=%s,
                  broker_sell_tax=%s,broker_other_cost=%s,broker_snapshot_at=%s,finalization_status=%s,
                  stable_confirmation_count=%s,fill_set_fingerprint=%s,last_stable_recheck_at=%s,
                  finalized_at=CASE WHEN %s='FINALIZED_BY_STABLE_RECHECK' THEN %s ELSE NULL END,updated_at=CURRENT_TIMESTAMP
                  WHERE broker_cost_snapshot_id=%s""",(raw.totals.buy_fee,raw.totals.sell_fee,raw.totals.sell_tax,
                  raw.totals.other_cost,raw.broker_snapshot_at,status.value,state.confirmation_count,fingerprint,
                  state.last_confirmed_at,status.value,self.clock(),snapshot_id));c.commit()
            if status is not BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK:continue
            grouped={}
            for _,_,trade_id,side,amount in fill_rows:grouped[(int(trade_id),str(side))]=grouped.get((int(trade_id),str(side)),Decimal('0'))+Decimal(amount)
            targets=tuple(CostAllocationTarget(k[0],k[1],v,f'{k[0]:020d}|{k[1]}') for k,v in grouped.items())
            _,allocations=allocate_final_costs(snapshot=state.snapshot,targets=targets,unattributed_activity=False)
            with self.connection_factory() as c,c.cursor() as q:
                for a in allocations:
                    q.execute("""INSERT INTO minute_ma_live_broker_cost_allocation(broker_cost_snapshot_id,minute_live_trade_id,
                      allocation_side,fill_notional,allocated_buy_fee,allocated_sell_fee,allocated_sell_tax,allocated_other_cost,
                      stable_allocation_key) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                      (snapshot_id,a.live_trade_id,a.side,a.fill_notional,a.buy_fee,a.sell_fee,a.sell_tax,a.other_cost,f'{a.live_trade_id:020d}|{a.side}'))
                c.commit();finalized+=1
            settled+=self._settle_due()
        return {'pending':len(rows),'finalized':finalized,'settled':settled}
    def _settle_due(self):
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("""SELECT t.minute_live_trade_id,t.entry_filled_amount,t.exit_filled_amount,
              COALESCE(sum(a.allocated_buy_fee),0),COALESCE(sum(a.allocated_sell_fee),0),
              COALESCE(sum(a.allocated_sell_tax),0),COALESCE(sum(a.allocated_other_cost),0)
              FROM minute_ma_live_trade t JOIN minute_ma_live_broker_cost_allocation a USING(minute_live_trade_id)
              JOIN minute_ma_live_broker_cost_snapshot s USING(broker_cost_snapshot_id)
              LEFT JOIN minute_ma_live_capital_settlement cs USING(minute_live_trade_id)
              WHERE t.trade_status='CLOSED' AND s.finalization_status='FINALIZED_BY_STABLE_RECHECK'
                AND cs.minute_live_trade_id IS NULL GROUP BY t.minute_live_trade_id,t.entry_filled_amount,t.exit_filled_amount
              HAVING count(DISTINCT a.allocation_side)=2 ORDER BY t.minute_live_trade_id""");rows=q.fetchall()
        applied=0
        for x in rows:
            amounts=SettlementAmounts(Decimal(x[1]),Decimal(x[2]),Decimal(x[3]),Decimal(x[4]),Decimal(x[5]),Decimal(x[6]))
            applied+=int(self.capital.apply_settlement(minute_live_trade_id=int(x[0]),amounts=amounts,settled_at=self.clock()))
        return applied
