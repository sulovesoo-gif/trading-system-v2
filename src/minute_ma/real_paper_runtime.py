"""Separated historical/incremental runtime for Minute-MA + REAL PAPER."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Iterable

from .contracts import Axis, MinuteBar, MinuteMaPath
from .engine import MinuteMaSignalEngine, SignalType
from .real_paper import (INITIAL_CAPITAL, CandidateTrade, RealFilter,
                         RealSnapshot, eligible_entry_time, k_mode,
                         passing_filters, replay_slots)


@dataclass(frozen=True)
class HistoricalResult:
    strategy_count: int
    variant_count: int
    common_entry_count: int
    paper_trade_count: int


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class MinuteMaRealPaperRuntime:
    """Produces only the new five-table REAL PAPER model.

    Existing Minute V1, LIVE and broker repositories are intentionally not
    imported, which makes STOP/EOD/SEND paths unreachable from this runtime.
    """

    def __init__(self, pool) -> None:
        self.pool = pool
        self.engine = MinuteMaSignalEngine()

    def _strategies(self) -> tuple[MinuteMaPath, ...]:
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
        for strategy in strategies: by_stock[strategy.signal_code].append(strategy)
        result: dict[tuple[int,RealFilter],list[tuple[CandidateTrade,RealSnapshot]]] = defaultdict(list)
        common_entries = 0
        market_dates: set[date] = set()
        for stock_code, group in by_stock.items():
            bars = self._bars(stock_code,start,end)
            bar_by_time = {bar.bar_time:bar for bar in bars}
            market_dates.update(bar.bar_time.date() for bar in bars)
            real_by_time = self._real(stock_code,start,end)
            # MA values are prepared exactly once per stock, not per REAL filter.
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
                    exit_bar=bar_by_time.get(exit_execution) if exit_execution is not None else None
                    if entry_bar is None:
                        continue
                    common_entries += 1
                    snapshot=real_by_time.get(entry.source_bar_time)
                    if snapshot is None:
                        continue
                    filters=passing_filters(snapshot)
                    if not filters:
                        continue
                    candidate=CandidateTrade(
                        self._trade_key(str(strategy.minute_path_id),entry.source_bar_time),
                        entry.source_bar_time.date(),entry_execution,
                        exit_execution if exit_bar is not None else None,
                        Decimal(str(entry_bar.open_price)),
                        Decimal(str(exit_bar.open_price)) if exit_bar is not None else None)
                    for filter_code in filters:
                        result[(strategy.minute_path_id,filter_code)].append((candidate,snapshot))
        return strategies, result, tuple(sorted(market_dates)), common_entries

    def backfill(self, start: date, end: date, *, dry_run: bool = False) -> HistoricalResult:
        strategies,candidates,market_dates,common_entries=self.build_candidates(start,end)
        strategy_by_id={row.minute_path_id:row for row in strategies}
        if dry_run:
            return HistoricalResult(len(strategies),len(strategies)*3,common_entries,
                                    sum(len(rows) for rows in candidates.values()))
        with self.pool.connection() as connection, connection.cursor() as cursor:
            for strategy in strategies:
                for filter_code in RealFilter:
                    rows=candidates.get((strategy.minute_path_id,filter_code),[])
                    slots=k_mode((row[0] for row in rows),market_dates=market_dates)
                    cursor.execute("""INSERT INTO minute_ma_real_variant(
                      minute_strategy_id,strategy_id,signal_code,filter_code,paper_epoch,k_mode,
                      initial_capital,effective_from)
                      VALUES(%s,%s,%s,%s,1,%s,%s,%s)
                      ON CONFLICT(minute_strategy_id,filter_code,paper_epoch) DO UPDATE
                      SET updated_at=CURRENT_TIMESTAMP
                      WHERE minute_ma_real_variant.k_mode=EXCLUDED.k_mode
                      RETURNING real_variant_id""",
                      (strategy.minute_path_id,str(strategy.minute_path_id),strategy.signal_code,
                       filter_code.value,slots,INITIAL_CAPITAL,start))
                    returned=cursor.fetchone()
                    if returned is None:
                        raise RuntimeError(
                            f"K_MODE epoch mismatch strategy={strategy.minute_path_id} "
                            f"filter={filter_code.value}; create a new epoch")
                    variant_id=int(returned[0])
                    slot_initial=INITIAL_CAPITAL/slots
                    for slot_no in range(1,slots+1):
                        cursor.execute("""INSERT INTO minute_ma_real_paper_slot(
                          real_variant_id,paper_epoch,slot_no,current_capital,occupancy_status)
                          VALUES(%s,1,%s,%s,'FREE') ON CONFLICT DO NOTHING""",
                          (variant_id,slot_no,slot_initial))
                    snapshots={row[0].key:row[1] for row in rows}
                    replayed=replay_slots((row[0] for row in rows),slot_count=slots)
                    trade_ids={}
                    for accounted in replayed:
                        snap=snapshots[accounted.candidate.key]
                        gross_return=(accounted.gross_pnl/(Decimal(accounted.quantity)*
                          accounted.candidate.entry_price) if accounted.quantity else Decimal(0))
                        net_return=(accounted.realized_pnl/accounted.capital_before
                          if accounted.capital_before else Decimal(0))
                        exit_signal=(accounted.candidate.exit_time-timedelta(minutes=1)
                                     if accounted.candidate.exit_time else None)
                        exit_reason='NORMAL_EXIT' if accounted.status=='CLOSED' else None
                        cursor.execute("""INSERT INTO minute_ma_real_paper_trade(
                          real_variant_id,minute_strategy_id,strategy_id,signal_code,filter_code,paper_epoch,
                          lifecycle_status,entry_signal_key,entry_signal_time,entry_execution_time,entry_price,
                          exit_signal_time,exit_execution_time,exit_price,exit_reason,slot_no,quantity,
                          capital_before,capital_after,gross_return,buy_fee,sell_fee,sell_tax,total_cost,
                          net_return,realized_pnl,velocity_value,velocity_avg_3,velocity_avg_10,
                          flow_avg_5,flow_avg_20,real_is_complete)
                          VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                          ON CONFLICT(real_variant_id,entry_signal_key) DO UPDATE
                          SET updated_at=minute_ma_real_paper_trade.updated_at
                          RETURNING real_paper_trade_id""",
                          (variant_id,strategy.minute_path_id,str(strategy.minute_path_id),
                           strategy.signal_code,filter_code.value,accounted.status,
                           accounted.candidate.key,accounted.candidate.entry_time-timedelta(minutes=1),
                           accounted.candidate.entry_time,accounted.candidate.entry_price,
                           exit_signal,accounted.candidate.exit_time,
                           accounted.candidate.exit_price,exit_reason,accounted.slot_no,accounted.quantity,
                           accounted.capital_before,accounted.capital_after,gross_return,
                           accounted.buy_fee,accounted.sell_fee,accounted.sell_tax,
                           accounted.buy_fee+accounted.sell_fee+accounted.sell_tax,net_return,
                           accounted.realized_pnl,snap.velocity_value,snap.velocity_avg_3,
                           snap.velocity_avg_10,snap.flow_avg_5,snap.flow_avg_20,snap.is_complete))
                        trade_ids[accounted.candidate.key]=int(cursor.fetchone()[0])
                    # Reconstruct the exact end-of-backfill slot state, including
                    # normal-exit responsibilities that remain OPEN at the cutoff.
                    final_capital={slot_no:slot_initial for slot_no in range(1,slots+1)}
                    open_by_slot={}
                    for accounted in replayed:
                        if accounted.slot_no is None: continue
                        if accounted.capital_after is not None:
                            final_capital[accounted.slot_no]=accounted.capital_after
                        if accounted.status=='OPEN':
                            open_by_slot[accounted.slot_no]=trade_ids[accounted.candidate.key]
                    for slot_no,capital in final_capital.items():
                        trade_id=open_by_slot.get(slot_no)
                        cursor.execute("""UPDATE minute_ma_real_paper_slot SET current_capital=%s,
                          occupancy_status=%s,current_trade_id=%s,version=version+1,
                          updated_at=CURRENT_TIMESTAMP
                          WHERE real_variant_id=%s AND paper_epoch=1 AND slot_no=%s""",
                          (capital,'OPEN' if trade_id else 'FREE',trade_id,variant_id,slot_no))
            connection.commit()
        return HistoricalResult(len(strategies),len(strategies)*3,common_entries,
                                sum(len(rows) for rows in candidates.values()))

    def process_day(self, trading_date: date) -> tuple[int, int]:
        """Incrementally apply executable events for one day.

        EXIT rows are handled before ENTRY rows at an equal execution minute.
        The unique entry key and row locks make repeated polling/restart safe.
        """
        strategies=self._strategies()
        by_stock: dict[str,list[MinuteMaPath]]=defaultdict(list)
        for strategy in strategies: by_stock[strategy.signal_code].append(strategy)
        opened=closed=0
        for stock_code,group in by_stock.items():
            bars=self._bars(stock_code,trading_date,trading_date)
            bar_by_time={bar.bar_time:bar for bar in bars}
            real_by_time=self._real(stock_code,trading_date,trading_date)
            points=self.engine.prepare(path=group[0],bars=bars)
            events=[]
            for strategy in group:
                for event in self.engine.evaluate_prepared(path=strategy,points=points):
                    execution_time=event.source_bar_time+timedelta(minutes=1)
                    if execution_time not in bar_by_time:
                        continue
                    order=0 if event.signal_type is SignalType.EXIT else 1
                    events.append((execution_time,order,strategy,event))
            for execution_time,_,strategy,event in sorted(
                    events,key=lambda row:(row[0],row[1],row[2].minute_path_id)):
                if event.signal_type is SignalType.EXIT:
                    closed += self._close_incremental(strategy,event,bar_by_time[execution_time])
                elif eligible_entry_time(event.source_bar_time):
                    snapshot=real_by_time.get(event.source_bar_time)
                    if snapshot is not None:
                        opened += self._open_incremental(
                            strategy,event,bar_by_time[execution_time],snapshot)
        return opened,closed

    def _open_incremental(self,strategy,event,execution_bar,snapshot:RealSnapshot) -> int:
        inserted=0
        filters=passing_filters(snapshot)
        if not filters:
            return 0
        key=self._trade_key(str(strategy.minute_path_id),event.source_bar_time)
        with self.pool.connection() as connection,connection.cursor() as cursor:
            for filter_code in filters:
                cursor.execute("""SELECT real_variant_id,paper_epoch FROM minute_ma_real_variant
                  WHERE minute_strategy_id=%s AND filter_code=%s AND enabled AND effective_to IS NULL""",
                  (strategy.minute_path_id,filter_code.value)); variant=cursor.fetchone()
                if variant is None:
                    continue
                variant_id,epoch=int(variant[0]),int(variant[1])
                cursor.execute("""SELECT slot_no,current_capital FROM minute_ma_real_paper_slot
                  WHERE real_variant_id=%s AND paper_epoch=%s AND occupancy_status='FREE'
                  ORDER BY slot_no FOR UPDATE SKIP LOCKED LIMIT 1""",(variant_id,epoch))
                slot=cursor.fetchone()
                status='OPEN' if slot else 'SKIPPED_NO_SLOT'
                capital=Decimal(str(slot[1])) if slot else None
                from .real_paper import BUY_FEE_RATE
                quantity=(int(capital/(Decimal(str(execution_bar.open_price))*(1+BUY_FEE_RATE)))
                          if capital else 0)
                if slot and quantity<=0: status='SKIPPED_QTY_ZERO'
                cursor.execute("""INSERT INTO minute_ma_real_paper_trade(
                  real_variant_id,minute_strategy_id,strategy_id,signal_code,filter_code,paper_epoch,
                  lifecycle_status,entry_signal_key,entry_signal_time,entry_execution_time,entry_price,
                  slot_no,quantity,capital_before,velocity_value,velocity_avg_3,velocity_avg_10,
                  flow_avg_5,flow_avg_20,real_is_complete)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
                  ON CONFLICT(real_variant_id,entry_signal_key) DO NOTHING RETURNING real_paper_trade_id""",
                  (variant_id,strategy.minute_path_id,str(strategy.minute_path_id),
                   strategy.signal_code,filter_code.value,epoch,status,key,event.source_bar_time,
                   execution_bar.bar_time,Decimal(str(execution_bar.open_price)),
                   int(slot[0]) if slot else None,quantity,capital,snapshot.velocity_value,
                   snapshot.velocity_avg_3,snapshot.velocity_avg_10,snapshot.flow_avg_5,
                   snapshot.flow_avg_20))
                row=cursor.fetchone()
                if row and status=='OPEN':
                    cursor.execute("""UPDATE minute_ma_real_paper_slot SET occupancy_status='OPEN',
                      current_trade_id=%s,version=version+1,updated_at=CURRENT_TIMESTAMP
                      WHERE real_variant_id=%s AND paper_epoch=%s AND slot_no=%s""",
                      (int(row[0]),variant_id,epoch,int(slot[0])))
                    inserted+=1
            connection.commit()
        return inserted

    def _close_incremental(self,strategy,event,execution_bar) -> int:
        count=0
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("""SELECT t.real_paper_trade_id,t.real_variant_id,t.paper_epoch,t.slot_no,
              t.quantity,t.entry_price,t.capital_before FROM minute_ma_real_paper_trade t
              WHERE t.minute_strategy_id=%s AND t.lifecycle_status='OPEN'
                AND t.entry_execution_time<=%s
              ORDER BY t.real_paper_trade_id FOR UPDATE""",
              (strategy.minute_path_id,event.source_bar_time))
            for trade_id,variant_id,epoch,slot_no,quantity,entry_price,capital_before in cursor.fetchall():
                qty=Decimal(quantity); entry=Decimal(str(entry_price)); exit_price=Decimal(str(execution_bar.open_price))
                from .real_paper import BUY_FEE_RATE,SELL_FEE_RATE,SELL_TAX_RATE
                gross=qty*(exit_price-entry); buy=qty*entry*BUY_FEE_RATE
                sell=qty*exit_price*SELL_FEE_RATE; tax=qty*exit_price*SELL_TAX_RATE
                realized=gross-buy-sell-tax; after=Decimal(str(capital_before))+realized
                cursor.execute("""UPDATE minute_ma_real_paper_trade SET lifecycle_status='CLOSED',
                  exit_signal_time=%s,exit_execution_time=%s,exit_price=%s,exit_reason='NORMAL_EXIT',
                  capital_after=%s,gross_return=%s,buy_fee=%s,sell_fee=%s,sell_tax=%s,total_cost=%s,
                  net_return=%s,realized_pnl=%s,updated_at=CURRENT_TIMESTAMP
                  WHERE real_paper_trade_id=%s AND lifecycle_status='OPEN'""",
                  (event.source_bar_time,execution_bar.bar_time,exit_price,after,
                   gross/(qty*entry),buy,sell,tax,buy+sell+tax,realized/Decimal(str(capital_before)),
                   realized,trade_id))
                cursor.execute("""UPDATE minute_ma_real_paper_slot SET current_capital=%s,
                  occupancy_status='FREE',current_trade_id=NULL,version=version+1,
                  updated_at=CURRENT_TIMESTAMP WHERE real_variant_id=%s AND paper_epoch=%s AND slot_no=%s""",
                  (after,variant_id,epoch,slot_no)); count+=1
            connection.commit()
        return count
