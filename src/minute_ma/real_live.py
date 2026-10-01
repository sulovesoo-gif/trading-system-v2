"""Minute-MA REAL V1.5 LIVE routes on the shared broker lifecycle."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .contracts import Axis, MinuteMaPath
from .engine import SignalEvent, SignalType
from .official_signals import OfficialSignalCycle, signal_evidence
from .overnight_exit import OvernightPolicy, OvernightEvent, reason_for, evidence_for
from .repository import PostgresMinuteMaRepository
from .real_paper import BUY_FEE_RATE, RealFilter, RealSnapshot, eligible_entry_time, passing_filters, purchasable_quantity


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def actual_order_quantity(*, sizing_mode: str, current_capital: Decimal,
                          fixed_quantity: int, reference_price: Decimal,
                          available_cash: Decimal) -> tuple[int,int,int]:
    """Return strategy target, cash-supported, and actual integer quantities."""
    target=(int(fixed_quantity) if sizing_mode=="FIXED_QTY"
            else purchasable_quantity(Decimal(current_capital),Decimal(reference_price)))
    cash_available=purchasable_quantity(Decimal(available_cash),Decimal(reference_price))
    return target,cash_available,min(target,cash_available)


@dataclass(frozen=True)
class RealLiveRoute:
    route_id: int
    variant_id: int
    filter_code: RealFilter
    path: MinuteMaPath
    route_code: str
    execution_stock_code: str
    sizing_mode: str
    allocated_amount: Decimal
    fixed_quantity: int
    capital_epoch_no: int
    activated_at: datetime
    cursor: datetime | None
    active: bool
    signal_effective_from: datetime | None = None


class PostgresMinuteMaRealLivePlanner:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    def rebase_route(self, *, real_live_route_id: int, allocated_amount: Decimal | None = None,
                     fixed_quantity: int | None = None, effective_from: datetime,
                     change_reason: str) -> int:
        """Start a new route/capital epoch; old OPEN trades retain the old route id."""
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("SELECT * FROM minute_ma_real_live_route WHERE real_live_route_id=%s AND effective_to IS NULL FOR UPDATE",
                      (real_live_route_id,)); old=q.fetchone()
            if old is None: raise ValueError("ACTIVE_REAL_ROUTE_REQUIRED")
            names=[column.name for column in q.description]; row=dict(zip(names,old))
            new_amount=Decimal(row["allocated_amount"] if allocated_amount is None else allocated_amount)
            new_qty=int(row["fixed_quantity"] if fixed_quantity is None else fixed_quantity)
            if row["sizing_mode"]=="CAPITAL": new_qty=0
            else: new_amount=Decimal(0)
            if new_amount==Decimal(row["allocated_amount"]) and new_qty==int(row["fixed_quantity"]):
                c.commit(); return int(real_live_route_id)
            q.execute("UPDATE minute_ma_real_live_route SET effective_to=%s,updated_at=CURRENT_TIMESTAMP WHERE real_live_route_id=%s",
                      (effective_from,real_live_route_id))
            q.execute("UPDATE minute_ma_real_live_capital SET ended_at=%s,updated_at=CURRENT_TIMESTAMP WHERE real_live_route_id=%s AND ended_at IS NULL",
                      (effective_from,real_live_route_id))
            epoch=int(row["capital_epoch_no"])+1
            q.execute("""INSERT INTO minute_ma_real_live_route(
              real_variant_id,minute_path_id,route_code,execution_stock_code,sizing_mode,
              allocated_amount,fixed_quantity,capital_epoch_no,activated_at,effective_from,
              last_source_bar_time,change_reason)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING real_live_route_id""",
              (row["real_variant_id"],row["minute_path_id"],row["route_code"],
               row["execution_stock_code"],row["sizing_mode"],new_amount,new_qty,epoch,
               effective_from,effective_from,effective_from,change_reason))
            new_id=int(q.fetchone()[0])
            q.execute("""INSERT INTO minute_ma_real_live_capital(
              real_live_route_id,capital_epoch_no,initial_capital,current_capital,effective_from,reset_reason)
              VALUES(%s,%s,%s,%s,%s,%s)""",
              (new_id,epoch,new_amount,new_amount,effective_from,change_reason))
            c.commit(); return new_id

    @staticmethod
    def _event_key(route: RealLiveRoute, event: SignalEvent) -> str:
        return _digest(f"MINUTE_REAL_V1_5|{route.variant_id}|{event.signal_event_key}")

    def plan_entry(self, *, route: RealLiveRoute, event: SignalEvent,
                   reference_price: Decimal, available_cash: Decimal) -> str:
        event_key=self._event_key(route,event)
        intent_key=_digest(f"MINUTE_REAL_V1_5|ENTRY|{route.route_id}|{event_key}")
        intent_id=str(uuid5(NAMESPACE_URL,"minute-real-intent|"+intent_key))
        request_id=str(uuid5(NAMESPACE_URL,"minute-real-request|"+intent_key))
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("""SELECT r.sizing_mode,r.allocated_amount,r.fixed_quantity,r.capital_epoch_no,
              COALESCE(c.current_capital,r.allocated_amount),r.execution_stock_code
              FROM minute_ma_real_live_route r LEFT JOIN minute_ma_real_live_capital c
                ON c.real_live_route_id=r.real_live_route_id
               AND c.capital_epoch_no=r.capital_epoch_no AND c.ended_at IS NULL
              WHERE r.real_live_route_id=%s AND r.effective_to IS NULL FOR UPDATE OF r""",
              (route.route_id,))
            row=q.fetchone()
            if row is None: return "ROUTE_NOT_ACTIVE"
            sizing,allocated,fixed,epoch,current,execution=row
            if sizing=="CAPITAL":
                q.execute("""SELECT current_capital FROM minute_ma_real_live_capital
                  WHERE real_live_route_id=%s AND capital_epoch_no=%s AND ended_at IS NULL
                  FOR UPDATE""",(route.route_id,epoch))
                capital_row=q.fetchone()
                if capital_row is None: return "CAPITAL_EPOCH_REQUIRED"
                current=capital_row[0]
            price=Decimal(reference_price); capital=Decimal(current)
            target_qty,_cash_available_qty,qty=actual_order_quantity(
              sizing_mode=str(sizing),current_capital=capital,fixed_quantity=int(fixed),
              reference_price=price,available_cash=Decimal(available_cash))
            required=price*qty*(Decimal(1)+BUY_FEE_RATE)
            q.execute("SELECT lifecycle_status FROM minute_ma_live_intent WHERE intent_key=%s",(intent_key,))
            prior=q.fetchone()
            if prior is not None: return str(prior[0])
            if qty<=0:
                reason=("ZERO_QUANTITY" if target_qty<=0
                        else "INSUFFICIENT_AVAILABLE_CASH")
                q.execute("""INSERT INTO minute_ma_real_live_entry_skip(
                  real_live_route_id,signal_event_key,source_event_time,capital_epoch_no,
                  capital_at_signal,planned_quantity,planned_notional,skip_reason)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                  (route.route_id,event_key,event.source_bar_time,epoch,capital,target_qty,
                   price*target_qty,reason))
                c.commit(); return reason
            signal_id=str(uuid5(NAMESPACE_URL,"minute-real-event|"+event_key+"|ENTRY"))
            q.execute("""INSERT INTO minute_ma_live_signal_event(
              minute_live_signal_event_id,minute_path_id,signal_event_key,event_type,source_bar_time,
              confirmed_at,source_snapshot,event_reason,signal_source,source_bar_finalized_at,
              evaluated_at,real_variant_id)
              VALUES(%s,%s,%s,'ENTRY',%s,%s,%s::jsonb,'REAL_VARIANT_ENTRY',%s,%s,
                     CURRENT_TIMESTAMP,%s) ON CONFLICT DO NOTHING""",
              (signal_id,route.path.minute_path_id,event_key,event.source_bar_time,event.confirmed_at,
               json.dumps({"filter":route.filter_code.value,**signal_evidence(event)}),event.signal_source,
               event.confirmed_at if event.signal_source.startswith("KIS_H0") else None,
               route.variant_id))
            q.execute("""INSERT INTO minute_ma_live_intent(
              intent_id,intent_key,minute_path_id,minute_live_signal_event_id,intent_type,
              source_event_time,reference_price,requested_quantity,capital_at_signal,lifecycle_status,
              real_variant_id,real_live_route_id,real_capital_epoch_no)
              VALUES(%s,%s,%s,%s,'ENTRY',%s,%s,%s,%s,'READY_FOR_BROKER',%s,%s,%s)""",
              (intent_id,intent_key,route.path.minute_path_id,signal_id,event.confirmed_at,price,qty,
               capital,route.variant_id,route.route_id,epoch))
            request_key=_digest("MINUTE_REAL_V1_5|REQUEST|"+intent_key+"|BUY")
            q.execute("""INSERT INTO live_order_request(
              order_request_id,idempotency_key,strategy_instance_id,source_intent_id,source_decision_id,
              execution_stock_code,side,requested_notional,requested_quantity,reference_price,order_type,
              execution_target_time,strategy_capital_before,reserved_capital,safety_status,status,reason,detail)
              VALUES(%s,%s,%s,%s,%s,%s,'BUY',%s,%s,%s,'MARKET',%s,%s,%s,'PASS',
                     'READY_FOR_BROKER','MINUTE_MA_REAL_LIVE',%s::jsonb)""",
              (request_id,request_key,f"MINUTE_REAL_ROUTE:{route.route_id}:EPOCH:{epoch}",intent_id,
               str(uuid5(NAMESPACE_URL,"minute-real-decision|"+intent_key)),execution,price*qty,qty,
               price,event.confirmed_at,capital,price*qty,json.dumps({"real_variant_id":route.variant_id,
                 "real_live_route_id":route.route_id,"route_code":route.route_code})))
            q.execute("INSERT INTO minute_ma_live_order_link(intent_id,order_request_id) VALUES(%s,%s)",
                      (intent_id,request_id))
            q.execute("""INSERT INTO minute_ma_live_capital_reservation(
              intent_id,reserved_amount,reservation_status) VALUES(%s,%s,'RESERVED')""",
              (intent_id,price*qty))
            c.commit(); return "READY_FOR_BROKER"

    def plan_exit(self, *, route: RealLiveRoute, event: SignalEvent,
                  reference_price: Decimal) -> dict[str,int]:
        event_key=self._event_key(route,event)
        signal_id=str(uuid5(NAMESPACE_URL,"minute-real-event|"+event_key+"|EXIT"))
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("""INSERT INTO minute_ma_live_signal_event(
              minute_live_signal_event_id,minute_path_id,signal_event_key,event_type,source_bar_time,
              confirmed_at,source_snapshot,event_reason,signal_source,source_bar_finalized_at,
              evaluated_at,real_variant_id)
              VALUES(%s,%s,%s,'EXIT',%s,%s,%s::jsonb,%s,%s,%s,
                     CURRENT_TIMESTAMP,%s) ON CONFLICT DO NOTHING""",
              (signal_id,route.path.minute_path_id,event_key,event.source_bar_time,event.confirmed_at,
               json.dumps({"filter":route.filter_code.value,**evidence_for(event)}),reason_for(event),event.signal_source,
               event.confirmed_at if event.signal_source.startswith("KIS_H0") else None,
               route.variant_id))
            q.execute("""SELECT t.minute_live_trade_id,t.ownership_id,t.capital_at_signal,
              t.real_capital_epoch_no,COALESCE(lp.quantity,0)
              FROM minute_ma_live_trade t LEFT JOIN execution_logical_position lp
                ON lp.ownership_type='MINUTE_MA' AND lp.ownership_id=t.ownership_id
               AND lp.stock_code=%s
              WHERE t.real_live_route_id=%s AND t.trade_status='OPEN'
                AND EXISTS (SELECT 1 FROM minute_ma_live_intent entry
                  WHERE entry.minute_live_trade_id=t.minute_live_trade_id AND entry.intent_type='ENTRY'
                    AND entry.source_event_time<=%s
                    AND (%s::date IS NULL OR entry.source_event_time::date<%s))
              ORDER BY t.minute_live_trade_id""",(route.execution_stock_code,route.route_id,
                event.source_bar_time,event.source_bar_time.date() if isinstance(event,OvernightEvent) else None,
                event.source_bar_time.date()))
            trades=q.fetchall(); c.commit()
        counts=defaultdict(int)
        for trade in trades:
            counts[self._plan_one_exit(route,event,signal_id,Decimal(reference_price),trade)]+=1
        return dict(counts)

    def has_open_trade(self, *, route: RealLiveRoute) -> bool:
        with self.connection_factory() as c,c.cursor() as q:
            q.execute("""SELECT 1 FROM minute_ma_live_trade
              WHERE real_live_route_id=%s AND trade_status='OPEN' LIMIT 1""",
              (route.route_id,))
            return q.fetchone() is not None

    def _plan_one_exit(self,route,event,signal_id,price,trade) -> str:
        trade_id,ownership,capital,epoch,qty=trade
        if int(qty)<=0: return "OWNERSHIP_REQUIRED"
        key=_digest(f"MINUTE_REAL_V1_5|EXIT|{trade_id}|{event.signal_event_key}")
        intent_id=str(uuid5(NAMESPACE_URL,"minute-real-intent|"+key))
        request_id=str(uuid5(NAMESPACE_URL,"minute-real-request|"+key))
        with self.connection_factory() as c,c.cursor() as q:
            # Serialize independent exit planners for this trade, not its route.
            q.execute("SELECT trade_status FROM minute_ma_live_trade WHERE minute_live_trade_id=%s FOR UPDATE",(trade_id,))
            current=q.fetchone()
            if current is None or current[0]!='OPEN': return 'TRADE_NOT_OPEN'
            q.execute("SELECT lifecycle_status FROM minute_ma_live_intent WHERE intent_key=%s",(key,))
            prior=q.fetchone()
            if prior is not None: return str(prior[0])
            q.execute("""SELECT 1 FROM minute_ma_live_intent WHERE target_minute_live_trade_id=%s
              AND intent_type='EXIT' AND lifecycle_status NOT IN ('REJECTED','CANCELLED','FAILED') LIMIT 1""",(trade_id,))
            if q.fetchone() is not None: return 'EXIT_ALREADY_PENDING'
            q.execute("""SELECT quantity FROM execution_logical_position WHERE ownership_type='MINUTE_MA'
              AND ownership_id=%s AND stock_code=%s""",(ownership,route.execution_stock_code))
            position=q.fetchone()
            qty=int(position[0]) if position else 0
            if qty<=0: return 'OWNERSHIP_REQUIRED'
            q.execute("""INSERT INTO minute_ma_live_intent(
              intent_id,intent_key,minute_path_id,minute_live_signal_event_id,minute_live_trade_id,
              intent_type,source_event_time,reference_price,requested_quantity,capital_at_signal,
              lifecycle_status,target_minute_live_trade_id,exit_reason,real_variant_id,
              real_live_route_id,real_capital_epoch_no)
              VALUES(%s,%s,%s,%s,%s,'EXIT',%s,%s,%s,%s,'READY_FOR_BROKER',%s,
                     %s,%s,%s,%s)""",
              (intent_id,key,route.path.minute_path_id,signal_id,trade_id,event.confirmed_at,price,
               qty,capital,trade_id,reason_for(event),route.variant_id,route.route_id,epoch))
            request_key=_digest("MINUTE_REAL_V1_5|REQUEST|"+key+"|SELL")
            q.execute("""INSERT INTO live_order_request(
              order_request_id,idempotency_key,strategy_instance_id,source_intent_id,source_decision_id,
              execution_stock_code,side,requested_notional,requested_quantity,reference_price,order_type,
              execution_target_time,strategy_capital_before,reserved_capital,safety_status,status,reason,detail)
              VALUES(%s,%s,%s,%s,%s,%s,'SELL',%s,%s,%s,'MARKET',%s,%s,0,'PASS',
                     'READY_FOR_BROKER',%s,%s::jsonb)""",
              (request_id,request_key,f"MINUTE_REAL_ROUTE:{route.route_id}:LIVE_TRADE:{trade_id}",
               intent_id,str(uuid5(NAMESPACE_URL,"minute-real-decision|"+key)),
               route.execution_stock_code,price*qty,qty,price,event.confirmed_at,capital,reason_for(event),
               json.dumps({"real_variant_id":route.variant_id,"real_live_route_id":route.route_id,
                 "minute_live_trade_id":trade_id,"ownership_id":ownership,
                 "exit_reason":reason_for(event),"exit_evidence":evidence_for(event)})))
            q.execute("INSERT INTO minute_ma_live_order_link(intent_id,order_request_id) VALUES(%s,%s)",
                      (intent_id,request_id))
            c.commit(); return "READY_FOR_BROKER"


class MinuteMaRealLiveRuntime:
    def __init__(self, *, pool, planner, price_lookup, cash_lookup, signals=None):
        self.pool=pool; self.planner=planner; self.price_lookup=price_lookup
        self.cash_lookup=cash_lookup; self.signals=signals

    def _routes(self) -> tuple[RealLiveRoute,...]:
        sql="""SELECT r.real_live_route_id,v.real_variant_id,v.filter_code,p.minute_path_id,pp.policy_path_key,
          p.data_axis,s.signal_code,s.direction,s.entry_fast_ma,s.entry_slow_ma,s.exit_fast_ma,
          s.exit_slow_ma,s.trend_ma,s.source_daily_strategy_id,r.route_code,r.execution_stock_code,
          r.sizing_mode,r.allocated_amount,r.fixed_quantity,r.capital_epoch_no,r.activated_at,
          r.last_source_bar_time,(r.effective_to IS NULL) AS active,v.forward_signal_from
          FROM minute_ma_real_live_route r
          JOIN minute_ma_real_variant v
            ON v.real_variant_id=r.real_variant_id
          LEFT JOIN minute_ma_path p
            ON p.minute_path_id=v.forward_minute_path_id
           AND p.minute_strategy_id=v.minute_strategy_id
          LEFT JOIN minute_ma_policy_path pp
            ON pp.minute_path_id=p.minute_path_id AND pp.is_enabled='Y'
          JOIN minute_ma_strategy_master s
            ON s.minute_strategy_id=v.minute_strategy_id
          WHERE v.enabled AND (r.effective_to IS NULL OR EXISTS(
            SELECT 1 FROM minute_ma_live_trade t WHERE t.real_live_route_id=r.real_live_route_id
              AND t.trade_status='OPEN')) ORDER BY r.real_live_route_id"""
        with self.pool.connection() as c,c.cursor() as q:
            q.execute(sql); rows=q.fetchall()
        result=[]
        for x in rows:
            if x[3] is None or x[4] is None or x[23] is None:
                raise ValueError('REAL_FORWARD_SIGNAL_MIGRATION_REQUIRED')
            path=MinuteMaPath(int(x[3]),str(x[4]),Axis(str(x[5])),str(x[6]),str(x[15]),
              str(x[7]),int(x[8]),int(x[9]),int(x[10]),int(x[11]),
              int(x[12]) if x[12] is not None else None,str(x[13]))
            result.append(RealLiveRoute(int(x[0]),int(x[1]),RealFilter(str(x[2])),path,str(x[14]),
              str(x[15]),str(x[16]),Decimal(str(x[17])),int(x[18]),int(x[19]),x[20],x[21],bool(x[22]),x[23]))
        return tuple(result)

    def _real(self, stock_code: str, trading_date: date) -> dict[datetime,RealSnapshot]:
        with self.pool.connection() as c,c.cursor() as q:
            q.execute("""SELECT bar_time,velocity_value,NULLIF(velocity_averages->>'3','')::numeric,
              NULLIF(velocity_averages->>'10','')::numeric,NULLIF(flow_averages->>'5','')::numeric,
              NULLIF(flow_averages->>'20','')::numeric,is_complete FROM flow_v3_minute_state
              WHERE stock_code=%s AND bar_time::date=%s""",(stock_code,trading_date))
            rows=q.fetchall()
        def d(v): return None if v is None else Decimal(str(v))
        return {r[0]:RealSnapshot(d(r[1]),d(r[2]),d(r[3]),d(r[4]),d(r[5]),bool(r[6])) for r in rows}

    def run_day(self, *, trading_date: date) -> dict[str,int]:
        routes=self._routes(); counts=defaultdict(int)
        signals=self.signals or OfficialSignalCycle(PostgresMinuteMaRepository(self.pool))
        overnight=OvernightPolicy(self.pool)
        now=datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)
        by_stock=defaultdict(list)
        for route in routes: by_stock[route.path.signal_code].append(route)
        for stock_code,group in by_stock.items():
            real=self._real(stock_code,trading_date)
            points,_=signals.day(path=group[0].path,trading_date=trading_date)
            if not points: continue
            by_strategy=defaultdict(list)
            for route in group: by_strategy[route.path.minute_path_id].append(route)
            for route_group in by_strategy.values():
                _,events=signals.day(path=route_group[0].path,trading_date=trading_date)
                forced=overnight.event(route_group[0].path,trading_date,
                    signals.v1_source_bars(stock_code=stock_code,trading_date=trading_date),events,now)
                if forced is not None:
                    events=tuple(events)+(forced,)
                for event in sorted(events,key=lambda e:(e.source_bar_time,0 if e.signal_type is SignalType.EXIT else 1)):
                    for route in route_group:
                        floor=route.cursor or route.activated_at
                        if route.signal_effective_from is None:
                            raise ValueError('REAL_FORWARD_SIGNAL_MIGRATION_REQUIRED')
                        floor=max(floor,route.signal_effective_from)
                        if event.source_bar_time<=floor and event.signal_type is SignalType.ENTRY: continue
                        if event.signal_type is SignalType.ENTRY:
                            if not route.active: continue
                            if route.cursor is None:
                                counts["BOOTSTRAPPED_NO_REPLAY"]+=1
                                continue
                            if not eligible_entry_time(event.source_bar_time): continue
                            if route.filter_code is not RealFilter.BASE:
                                snapshot=real.get(event.source_bar_time)
                                if snapshot is None or route.filter_code not in passing_filters(snapshot): continue
                            price=Decimal(self.price_lookup.current_price(route.execution_stock_code))
                            cash=self.cash_lookup.orderable_cash(stock_code=route.execution_stock_code,
                              order_price=price,order_division='01').amount
                            counts[self.planner.plan_entry(route=route,event=event,
                              reference_price=price,available_cash=cash)]+=1
                        else:
                            if not self.planner.has_open_trade(route=route): continue
                            price=Decimal(self.price_lookup.current_price(route.execution_stock_code))
                            for status,n in self.planner.plan_exit(route=route,event=event,
                              reference_price=price).items(): counts[status]+=n
            latest=points[-1].bar_time
            with self.pool.connection() as c,c.cursor() as q:
                q.execute("""UPDATE minute_ma_real_live_route SET last_source_bar_time=%s,
                  updated_at=CURRENT_TIMESTAMP WHERE real_live_route_id=ANY(%s)
                  AND (last_source_bar_time IS NULL OR last_source_bar_time<%s)""",
                  (latest,[r.route_id for r in group],latest)); c.commit()
        return dict(counts)
