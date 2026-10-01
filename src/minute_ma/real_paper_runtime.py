"""Isolated historical/incremental runtime for Minute-MA + REAL PAPER V1.3."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

from .contracts import Axis, MinuteBar, MinuteMaPath
from .engine import MinuteMaSignalEngine, SignalType
from .official_signals import OfficialSignalCycle, signal_evidence
from .overnight_exit import OvernightPolicy, OvernightEvent, reason_for, evidence_for
from .repository import PostgresMinuteMaRepository
from .real_paper import (
    INITIAL_CAPITAL, CandidateTrade, RealFilter, RealSnapshot, account_costs,
    eligible_entry_time, passing_filters, purchasable_quantity,
    replay_parallel_capital,
)


@dataclass(frozen=True)
class HistoricalResult:
    strategy_count: int
    variant_count: int
    common_entry_count: int
    paper_trade_count: int


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class MinuteMaRealPaperRuntime:
    """Uses only RAW, completed REAL state and the additive research tables."""

    def __init__(self, pool) -> None:
        self.pool = pool
        self.engine = MinuteMaSignalEngine()

    def _strategies(self) -> tuple[MinuteMaPath, ...]:
        # Frozen historical backfill contract only. Forward uses V1 below.
        sql = """SELECT minute_strategy_id,source_daily_strategy_id,signal_code,
                        direction,entry_fast_ma,entry_slow_ma,exit_fast_ma,exit_slow_ma,trend_ma
                   FROM minute_ma_strategy_master
                  WHERE is_enabled='Y' AND direction='LONG'
                  ORDER BY minute_strategy_id"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql); rows = cursor.fetchall()
        return tuple(MinuteMaPath(
            int(row[0]), f"MINUTE_REAL_{row[0]}", Axis.KRX_RESET,
            str(row[2]), str(row[2]), str(row[3]), int(row[4]), int(row[5]),
            int(row[6]), int(row[7]), int(row[8]) if row[8] is not None else None,
            str(row[1]),
        ) for row in rows)

    def _bars(self, stock_code: str, start: date, end: date) -> tuple[MinuteBar, ...]:
        sql = """SELECT DISTINCT ON (bar_time) bar_time,open_price,high_price,low_price,
                        close_price,volume
                   FROM raw_stock_minute
                  WHERE stock_code=%s AND data_source='KIS' AND trading_venue='KRX'
                    AND collect_cycle='1MIN' AND bar_time::date BETWEEN %s AND %s
                    AND bar_time::time BETWEEN TIME '09:00' AND TIME '15:30'
                  ORDER BY bar_time,collected_at DESC NULLS LAST"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql, (stock_code,start,end)); rows = cursor.fetchall()
        return tuple(MinuteBar(row[0],float(row[1]),float(row[2]),float(row[3]),
                               float(row[4]),int(row[5] or 0)) for row in rows)

    def _real(self, stock_code: str, start: date, end: date) -> dict[datetime, RealSnapshot]:
        sql = """SELECT bar_time,velocity_value,
                        NULLIF(velocity_averages->>'3','')::numeric,
                        NULLIF(velocity_averages->>'10','')::numeric,
                        NULLIF(flow_averages->>'5','')::numeric,
                        NULLIF(flow_averages->>'20','')::numeric,is_complete
                   FROM flow_v3_minute_state
                  WHERE stock_code=%s AND bar_time::date BETWEEN %s AND %s"""
        with self.pool.connection() as connection, connection.cursor() as cursor:
            cursor.execute(sql,(stock_code,start,end)); rows=cursor.fetchall()
        return {row[0]:RealSnapshot(_decimal(row[1]),_decimal(row[2]),_decimal(row[3]),
                                    _decimal(row[4]),_decimal(row[5]),bool(row[6]))
                for row in rows}

    @staticmethod
    def _trade_key(strategy_id: str, entry: datetime) -> str:
        return sha256(f"MINUTE_REAL_V1|{strategy_id}|{entry.isoformat()}".encode()).hexdigest()

    def build_candidates(self, start: date, end: date):
        strategies = self._strategies()
        by_stock: dict[str,list[MinuteMaPath]] = defaultdict(list)
        for strategy in strategies:
            by_stock[strategy.signal_code].append(strategy)
        result: dict[tuple[int,RealFilter],list[tuple[CandidateTrade,RealSnapshot]]] = defaultdict(list)
        common_entries = 0
        for stock_code, group in by_stock.items():
            bars = self._bars(stock_code,start,end)
            bar_by_time = {bar.bar_time:bar for bar in bars}
            real_by_time = self._real(stock_code,start,end)
            points = self.engine.prepare(path=group[0], bars=bars)
            for strategy in group:
                events = self.engine.evaluate_prepared(path=strategy,points=points)
                exits = [event for event in events if event.signal_type is SignalType.EXIT]
                for entry in (event for event in events
                              if event.signal_type is SignalType.ENTRY
                              and eligible_entry_time(event.source_bar_time)):
                    exit_event=next((event for event in exits
                                     if event.source_bar_time>entry.source_bar_time),None)
                    entry_execution=entry.source_bar_time+timedelta(minutes=1)
                    exit_execution=(exit_event.source_bar_time+timedelta(minutes=1)
                                    if exit_event is not None else None)
                    entry_bar=bar_by_time.get(entry_execution)
                    exit_bar=bar_by_time.get(exit_execution) if exit_execution else None
                    if entry_bar is None:
                        continue
                    common_entries += 1
                    candidate=CandidateTrade(
                        self._trade_key(str(strategy.minute_path_id),entry.source_bar_time),
                        entry.source_bar_time.date(),entry_execution,
                        exit_execution if exit_bar is not None else None,
                        Decimal(str(entry_bar.open_price)),
                        Decimal(str(exit_bar.open_price)) if exit_bar is not None else None)
                    base_snapshot=RealSnapshot(None,None,None,None,None,False)
                    result[(strategy.minute_path_id,RealFilter.BASE)].append((candidate,base_snapshot))
                    snapshot=real_by_time.get(entry.source_bar_time)
                    if snapshot is not None:
                        for filter_code in passing_filters(snapshot):
                            result[(strategy.minute_path_id,filter_code)].append((candidate,snapshot))
        return strategies, result, common_entries

    def backfill(self, start: date, end: date, *, dry_run: bool = False,
                 filter_codes: tuple[RealFilter,...] | None = None) -> HistoricalResult:
        strategies,candidates,common_entries=self.build_candidates(start,end)
        selected=filter_codes or tuple(RealFilter)
        if dry_run:
            return HistoricalResult(len(strategies),len(strategies)*len(selected),common_entries,
                                    sum(len(candidates.get((strategy.minute_path_id,code),()))
                                        for strategy in strategies for code in selected))
        with self.pool.connection() as connection, connection.cursor() as cursor:
            for strategy in strategies:
                for filter_code in selected:
                    cursor.execute("""INSERT INTO minute_ma_real_variant(
                      minute_strategy_id,strategy_id,signal_code,filter_code,paper_epoch,
                      initial_capital,effective_from)
                      VALUES(%s,%s,%s,%s,1,%s,%s)
                      ON CONFLICT(minute_strategy_id,filter_code,paper_epoch) DO UPDATE
                      SET updated_at=CURRENT_TIMESTAMP RETURNING real_variant_id""",
                      (strategy.minute_path_id,str(strategy.minute_path_id),strategy.signal_code,
                       filter_code.value,INITIAL_CAPITAL,start))
                    variant_id=int(cursor.fetchone()[0])
                    rows=candidates.get((strategy.minute_path_id,filter_code),[])
                    snapshots={row[0].key:row[1] for row in rows}
                    replay=replay_parallel_capital((row[0] for row in rows))
                    cursor.execute("""INSERT INTO minute_ma_real_capital_epoch(
                      real_variant_id,paper_epoch,initial_capital,current_realized_capital,
                      effective_from,reset_reason)
                      VALUES(%s,1,%s,%s,%s,'INITIAL_V1_5_BASE')
                      ON CONFLICT(real_variant_id,paper_epoch) DO UPDATE SET
                        current_realized_capital=EXCLUDED.current_realized_capital,
                        version=minute_ma_real_capital_epoch.version+1,
                        updated_at=CURRENT_TIMESTAMP
                      WHERE minute_ma_real_capital_epoch.current_realized_capital
                            IS DISTINCT FROM EXCLUDED.current_realized_capital""",
                      (variant_id,INITIAL_CAPITAL,replay.current_realized_capital,start))
                    for accounted in replay.trades:
                        snap=snapshots[accounted.candidate.key]
                        compound=accounted.compound; fixed=accounted.fixed
                        closed=accounted.status=='CLOSED'
                        exit_signal=(accounted.candidate.exit_time-timedelta(minutes=1)
                                     if closed else None)
                        compound_base=Decimal(compound.quantity)*accounted.candidate.entry_price
                        fixed_base=Decimal(fixed.quantity)*accounted.candidate.entry_price
                        cursor.execute("""INSERT INTO minute_ma_real_paper_trade(
                          real_variant_id,minute_strategy_id,strategy_id,signal_code,filter_code,paper_epoch,
                          lifecycle_status,entry_signal_key,entry_signal_time,entry_execution_time,entry_price,
                          exit_signal_time,exit_execution_time,exit_price,exit_reason,
                          compound_quantity,entry_realized_capital,settlement_realized_capital_after,
                          compound_gross_pnl,compound_gross_return,compound_buy_fee,compound_sell_fee,
                          compound_sell_tax,compound_total_cost,compound_net_return,compound_realized_pnl,
                          fixed_quantity,fixed_gross_pnl,fixed_gross_return,fixed_buy_fee,fixed_sell_fee,
                          fixed_sell_tax,fixed_total_cost,fixed_net_return,fixed_realized_pnl,settlement_time,
                          velocity_value,velocity_avg_3,velocity_avg_10,flow_avg_5,flow_avg_20,real_is_complete)
                          VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                 %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                 %s,%s,%s,%s,%s,%s)
                          ON CONFLICT(real_variant_id,entry_signal_key) DO UPDATE SET
                            lifecycle_status=EXCLUDED.lifecycle_status,
                            exit_signal_time=EXCLUDED.exit_signal_time,
                            exit_execution_time=EXCLUDED.exit_execution_time,
                            exit_price=EXCLUDED.exit_price,exit_reason=EXCLUDED.exit_reason,
                            compound_quantity=EXCLUDED.compound_quantity,
                            entry_realized_capital=EXCLUDED.entry_realized_capital,
                            settlement_realized_capital_after=EXCLUDED.settlement_realized_capital_after,
                            compound_gross_pnl=EXCLUDED.compound_gross_pnl,
                            compound_gross_return=EXCLUDED.compound_gross_return,
                            compound_buy_fee=EXCLUDED.compound_buy_fee,compound_sell_fee=EXCLUDED.compound_sell_fee,
                            compound_sell_tax=EXCLUDED.compound_sell_tax,compound_total_cost=EXCLUDED.compound_total_cost,
                            compound_net_return=EXCLUDED.compound_net_return,
                            compound_realized_pnl=EXCLUDED.compound_realized_pnl,
                            fixed_quantity=EXCLUDED.fixed_quantity,fixed_gross_pnl=EXCLUDED.fixed_gross_pnl,
                            fixed_gross_return=EXCLUDED.fixed_gross_return,fixed_buy_fee=EXCLUDED.fixed_buy_fee,
                            fixed_sell_fee=EXCLUDED.fixed_sell_fee,fixed_sell_tax=EXCLUDED.fixed_sell_tax,
                            fixed_total_cost=EXCLUDED.fixed_total_cost,fixed_net_return=EXCLUDED.fixed_net_return,
                            fixed_realized_pnl=EXCLUDED.fixed_realized_pnl,
                            settlement_time=EXCLUDED.settlement_time,updated_at=CURRENT_TIMESTAMP""",
                          (variant_id,strategy.minute_path_id,str(strategy.minute_path_id),strategy.signal_code,
                           filter_code.value,accounted.status,accounted.candidate.key,
                           accounted.candidate.entry_time-timedelta(minutes=1),accounted.candidate.entry_time,
                           accounted.candidate.entry_price,exit_signal,accounted.candidate.exit_time,
                           accounted.candidate.exit_price,'NORMAL_EXIT' if closed else None,
                           compound.quantity,accounted.entry_realized_capital,
                           accounted.settlement_realized_capital_after,compound.gross_pnl,
                           compound.gross_pnl/compound_base if compound_base else Decimal(0),
                           compound.buy_fee,compound.sell_fee,compound.sell_tax,
                           compound.buy_fee+compound.sell_fee+compound.sell_tax,
                           compound.realized_pnl/accounted.entry_realized_capital
                           if accounted.entry_realized_capital else Decimal(0),compound.realized_pnl,
                           fixed.quantity,fixed.gross_pnl,
                           fixed.gross_pnl/fixed_base if fixed_base else Decimal(0),fixed.buy_fee,
                           fixed.sell_fee,fixed.sell_tax,fixed.buy_fee+fixed.sell_fee+fixed.sell_tax,
                           fixed.realized_pnl/INITIAL_CAPITAL,fixed.realized_pnl,
                           accounted.candidate.exit_time if closed else None,snap.velocity_value,
                           snap.velocity_avg_3,snap.velocity_avg_10,snap.flow_avg_5,snap.flow_avg_20,
                           snap.is_complete))
            connection.commit()
        return HistoricalResult(len(strategies),len(strategies)*len(selected),common_entries,
                                sum(len(candidates.get((strategy.minute_path_id,code),()))
                                    for strategy in strategies for code in selected))

    def process_day(self, trading_date: date) -> tuple[int, int]:
        strategies=self._forward_strategies(); by_stock=defaultdict(list)
        for item in strategies: by_stock[item[1].signal_code].append(item)
        signals=OfficialSignalCycle(PostgresMinuteMaRepository(self.pool))
        overnight=OvernightPolicy(self.pool)
        now=datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)
        opened=closed=0
        for stock_code,group in by_stock.items():
            # KRX is retained solely as the historical PAPER execution-price
            # proxy. These prices never enter MA preparation/evaluation.
            bars=self._bars(stock_code,trading_date,trading_date)
            bar_by_time={bar.bar_time:bar for bar in bars}
            real_by_time=self._real(stock_code,trading_date,trading_date)
            events=[]
            for strategy_id,path,cutover in group:
                _,official_events=signals.day(path=path,trading_date=trading_date)
                forced=overnight.event(path,trading_date,signals.v1_source_bars(
                    stock_code=stock_code,trading_date=trading_date),official_events,now)
                if forced is not None and forced.source_bar_time in bar_by_time:
                    events.append((forced.source_bar_time,0,
                                   replace(path,minute_path_id=strategy_id),forced))
                for event in official_events:
                    if event.source_bar_time<=cutover: continue
                    execution_time=event.source_bar_time+timedelta(minutes=1)
                    if execution_time in bar_by_time:
                        # Persistence APIs historically use this field as the
                        # strategy id; the signal itself retains official path id.
                        strategy=replace(path,minute_path_id=strategy_id)
                        events.append((execution_time,0 if event.signal_type is SignalType.EXIT else 1,
                                       strategy,event))
            for execution_time,_,strategy,event in sorted(
                    events,key=lambda row:(row[0],row[1],row[2].minute_path_id)):
                if event.signal_type is SignalType.EXIT:
                    closed+=self._close_incremental(strategy,event,bar_by_time[execution_time])
                elif eligible_entry_time(event.source_bar_time):
                    snapshot=real_by_time.get(event.source_bar_time)
                    opened+=self._open_incremental(strategy,event,bar_by_time[execution_time],snapshot)
        return opened,closed

    def _forward_strategies(self):
        with self.pool.connection() as c,c.cursor() as q:
            q.execute("""SELECT DISTINCT s.minute_strategy_id,p.minute_path_id,pp.policy_path_key,
              p.data_axis,s.signal_code,s.direction,s.entry_fast_ma,s.entry_slow_ma,
              s.exit_fast_ma,s.exit_slow_ma,s.trend_ma,s.source_daily_strategy_id,v.forward_signal_from
              FROM minute_ma_real_variant v
              JOIN minute_ma_strategy_master s ON s.minute_strategy_id=v.minute_strategy_id
              LEFT JOIN minute_ma_path p ON p.minute_path_id=v.forward_minute_path_id
                AND p.minute_strategy_id=s.minute_strategy_id
              LEFT JOIN minute_ma_policy_path pp ON pp.minute_path_id=p.minute_path_id AND pp.is_enabled='Y'
              WHERE v.enabled AND v.effective_to IS NULL AND s.is_enabled='Y' AND s.direction='LONG'
              ORDER BY s.minute_strategy_id""")
            rows=q.fetchall()
        if any(r[1] is None or r[2] is None or r[12] is None for r in rows):
            raise ValueError('REAL_FORWARD_SIGNAL_MIGRATION_REQUIRED')
        return tuple((int(r[0]),MinuteMaPath(int(r[1]),str(r[2]),Axis(r[3]),str(r[4]),str(r[4]),
            str(r[5]),int(r[6]),int(r[7]),int(r[8]),int(r[9]),
            int(r[10]) if r[10] is not None else None,str(r[11])),r[12]) for r in rows)

    def _open_incremental(self,strategy,event,execution_bar,snapshot:RealSnapshot|None) -> int:
        inserted=0
        filters=(RealFilter.BASE,)+(() if snapshot is None else passing_filters(snapshot))
        key=self._trade_key(str(strategy.minute_path_id),event.source_bar_time)
        with self.pool.connection() as connection,connection.cursor() as cursor:
            for filter_code in filters:
                cursor.execute("""SELECT v.real_variant_id,v.paper_epoch,c.current_realized_capital
                  FROM minute_ma_real_variant v JOIN minute_ma_real_capital_epoch c
                    ON c.real_variant_id=v.real_variant_id AND c.paper_epoch=v.paper_epoch AND c.ended_at IS NULL
                  WHERE v.minute_strategy_id=%s AND v.filter_code=%s AND v.enabled
                    AND v.effective_to IS NULL AND v.forward_signal_from<%s
                    AND v.forward_minute_path_id=%s FOR UPDATE OF c""",
                  (strategy.minute_path_id,filter_code.value,event.source_bar_time,event.minute_path_id)); variant=cursor.fetchone()
                if variant is None: continue
                variant_id,epoch,capital=int(variant[0]),int(variant[1]),Decimal(str(variant[2]))
                price=Decimal(str(execution_bar.open_price))
                compound_qty=purchasable_quantity(capital,price)
                fixed_qty=purchasable_quantity(INITIAL_CAPITAL,price)
                cursor.execute("""INSERT INTO minute_ma_real_paper_trade(
                  real_variant_id,minute_strategy_id,strategy_id,signal_code,filter_code,paper_epoch,
                  lifecycle_status,entry_signal_key,entry_signal_time,entry_execution_time,entry_price,
                  compound_quantity,fixed_quantity,entry_realized_capital,velocity_value,velocity_avg_3,
                  velocity_avg_10,flow_avg_5,flow_avg_20,real_is_complete,entry_signal_evidence)
                  VALUES(%s,%s,%s,%s,%s,%s,'OPEN',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                  ON CONFLICT(real_variant_id,entry_signal_key) DO NOTHING""",
                  (variant_id,strategy.minute_path_id,str(strategy.minute_path_id),strategy.signal_code,
                   filter_code.value,epoch,key,event.source_bar_time,execution_bar.bar_time,price,
                    compound_qty,fixed_qty,capital,
                    None if snapshot is None else snapshot.velocity_value,
                    None if snapshot is None else snapshot.velocity_avg_3,
                    None if snapshot is None else snapshot.velocity_avg_10,
                    None if snapshot is None else snapshot.flow_avg_5,
                    None if snapshot is None else snapshot.flow_avg_20,
                    False if snapshot is None else snapshot.is_complete,json.dumps(signal_evidence(event))))
                inserted+=cursor.rowcount
            connection.commit()
        return inserted

    def _close_incremental(self,strategy,event,execution_bar) -> int:
        count=0; exit_price=Decimal(str(execution_bar.open_price))
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("""SELECT t.real_paper_trade_id,t.real_variant_id,t.paper_epoch,
              t.compound_quantity,t.fixed_quantity,t.entry_price
              FROM minute_ma_real_paper_trade t
              WHERE t.minute_strategy_id=%s AND t.lifecycle_status='OPEN'
                AND t.entry_execution_time<=%s
                AND (%s::date IS NULL OR t.entry_execution_time::date<%s)
              ORDER BY t.real_variant_id,t.real_paper_trade_id FOR UPDATE OF t""",
              (strategy.minute_path_id,event.source_bar_time,
               event.source_bar_time.date() if isinstance(event,OvernightEvent) else None,
               event.source_bar_time.date())); rows=cursor.fetchall()
            by_variant=defaultdict(list)
            for row in rows: by_variant[(int(row[1]),int(row[2]))].append(row)
            for (variant_id,epoch),trades in by_variant.items():
                cursor.execute("""SELECT current_realized_capital FROM minute_ma_real_capital_epoch
                  WHERE real_variant_id=%s AND paper_epoch=%s FOR UPDATE""",
                  (variant_id,epoch)); capital=Decimal(str(cursor.fetchone()[0]))
                for trade_id,_,_,compound_qty,fixed_qty,entry_price in trades:
                    entry=Decimal(str(entry_price))
                    compound=account_costs(int(compound_qty),entry,exit_price)
                    fixed=account_costs(int(fixed_qty),entry,exit_price)
                    capital+=compound.realized_pnl
                    compound_base=Decimal(compound.quantity)*entry
                    fixed_base=Decimal(fixed.quantity)*entry
                    cursor.execute("""UPDATE minute_ma_real_paper_trade SET lifecycle_status='CLOSED',
                      exit_signal_time=%s,exit_execution_time=%s,exit_price=%s,exit_reason=%s,
                      settlement_time=%s,settlement_realized_capital_after=%s,
                      compound_gross_pnl=%s,compound_gross_return=%s,compound_buy_fee=%s,
                      compound_sell_fee=%s,compound_sell_tax=%s,compound_total_cost=%s,
                      compound_net_return=%s,compound_realized_pnl=%s,
                      fixed_gross_pnl=%s,fixed_gross_return=%s,fixed_buy_fee=%s,fixed_sell_fee=%s,
                      fixed_sell_tax=%s,fixed_total_cost=%s,fixed_net_return=%s,fixed_realized_pnl=%s,
                      exit_signal_evidence=%s::jsonb,
                      updated_at=CURRENT_TIMESTAMP WHERE real_paper_trade_id=%s AND lifecycle_status='OPEN'""",
                      (event.source_bar_time,execution_bar.bar_time,exit_price,reason_for(event),execution_bar.bar_time,capital,
                       compound.gross_pnl,compound.gross_pnl/compound_base if compound_base else Decimal(0),
                       compound.buy_fee,compound.sell_fee,compound.sell_tax,
                       compound.buy_fee+compound.sell_fee+compound.sell_tax,
                       compound.realized_pnl/(capital-compound.realized_pnl)
                       if capital!=compound.realized_pnl else Decimal(0),compound.realized_pnl,
                       fixed.gross_pnl,fixed.gross_pnl/fixed_base if fixed_base else Decimal(0),
                       fixed.buy_fee,fixed.sell_fee,fixed.sell_tax,
                       fixed.buy_fee+fixed.sell_fee+fixed.sell_tax,fixed.realized_pnl/INITIAL_CAPITAL,
                       fixed.realized_pnl,json.dumps(evidence_for(event)),trade_id)); count+=cursor.rowcount
                cursor.execute("""UPDATE minute_ma_real_capital_epoch SET current_realized_capital=%s,
                  version=version+1,updated_at=CURRENT_TIMESTAMP
                  WHERE real_variant_id=%s AND paper_epoch=%s""",
                  (capital,variant_id,epoch))
            connection.commit()
        return count
