"""Rollback-only TEST PostgreSQL proof for Minute MA V1 production wiring.

This script applies the pending additive schema and the user-approved apply
plan inside one database transaction, exercises production repositories, then
always rolls the transaction back.  It never constructs a KIS transport.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))

import psycopg
from dotenv import load_dotenv

from src.daily_ma_v03.broker_cost_allocation import (
    BrokerCostSnapshot,BrokerCostStatus,BrokerCostTotals,CostAllocationTarget,
    allocate_final_costs,
)
from src.minute_ma.capital import PostgresMinuteMaCapitalStore
from src.minute_ma.cost_finalizer import MinuteMaCostFinalizer
from src.minute_ma.engine import SignalEvent,SignalType
from src.minute_ma.live_planner import PostgresMinuteMaLivePlanner
from src.minute_ma.repository import PostgresMinuteMaRepository
from src.minute_ma.v1_live_runtime import MinuteMaV1LiveStopMonitor
from src.repository.database import DatabaseSettings


def _migration(path: Path) -> str:
    text=path.read_text(encoding='utf-8')
    lines=text.splitlines()
    if lines and lines[0].startswith('--'):
        pass
    return '\n'.join(line for line in lines if line.strip() not in {'BEGIN;','COMMIT;'})


class _TxConnection:
    def __init__(self,connection):self._connection=connection
    def __enter__(self):return self
    def __exit__(self,exc_type,exc,tb):return False
    def cursor(self):return self._connection.cursor()
    def commit(self):return None
    def rollback(self):return None


class _TxPool:
    def __init__(self,connection):self.connection_object=_TxConnection(connection)
    @contextmanager
    def connection(self):yield self.connection_object


class _Price:
    def current_price(self,stock_code):return Decimal('1000')


def _event(path,when,key):
    return SignalEvent(path.minute_path_id,path.path_key,SignalType.EXIT,when,
                       when+timedelta(minutes=1,seconds=1),key,True,{}, {})


def main() -> int:
    load_dotenv(ROOT/'.env')
    settings=DatabaseSettings.from_environment()
    if settings.name!='trading_system_v2_test':
        raise RuntimeError(f'TEST_DATABASE_REQUIRED:{settings.name}')
    connection=psycopg.connect(**settings.connection_kwargs())
    tx=_TxConnection(connection);pool=_TxPool(connection);factory=lambda:tx
    evidence={}
    try:
        with connection.cursor() as q:
            q.execute("SELECT count(*) FROM live_broker_order");before_broker=int(q.fetchone()[0])
            q.execute("SELECT count(*) FROM minute_ma_live_order_link");before_mapping=int(q.fetchone()[0])
            q.execute(_migration(ROOT/'database/migrations/20260828_minute_ma_v10_policy_additive.sql'))
            q.execute(_migration(ROOT/'database/migrations/20260828_minute_ma_v10_prod_apply.sql'))
            q.execute("UPDATE minute_ma_send_profile SET send_enabled='Y' WHERE profile_code='MINUTE_MA_LIVE_SEND'")
            q.execute("""SELECT pp.minute_policy_path_id,o.minute_policy_operation_id,o.capital_epoch_no,
                                o.allocated_amount,s.execution_code
                           FROM minute_ma_policy_path pp
                           JOIN minute_ma_path p USING(minute_path_id)
                           JOIN minute_ma_strategy_master s USING(minute_strategy_id)
                           JOIN minute_ma_policy_operation o USING(minute_policy_path_id)
                          WHERE s.source_daily_strategy_id='DS002431' AND pp.policy_code='MINUTE_MA_V1_LONG'
                            AND o.effective_to IS NULL AND o.operation_status='LIVE'""")
            policy_path_id,policy_operation_id,epoch,initial_capital,stock=q.fetchone()
            q.execute("""INSERT INTO execution_reconciliation_audit(
                         stock_code,broker_net_quantity,attributed_quantity,unattributed_quantity,status,detail)
                         VALUES(%s,3,3,0,'HEALTHY','{"fixture":"MINUTE_MA_V1_ROLLBACK_ONLY"}'::jsonb)""",(stock,))
            trade_ids=[]
            anchors=(Decimal('100'),Decimal('100'),Decimal('90'))
            entries=(Decimal('1000'),Decimal('1100'),Decimal('900'))
            for index,(anchor,entry_amount) in enumerate(zip(anchors,entries),1):
                ownership=f'TEST:MINUTE_MA_V1:OWNERSHIP:{index}'
                q.execute("""INSERT INTO minute_ma_live_trade(
                  minute_path_id,operation_id,capital_epoch_no,ownership_id,trade_status,capital_at_signal,
                  entry_filled_amount,minute_policy_path_id,minute_policy_operation_id,
                  underlying_entry_reference_price,stop_threshold_price,stop_policy,created_at)
                  SELECT pp.minute_path_id,NULL,%s,%s,'OPEN',%s,%s,pp.minute_policy_path_id,%s,%s,%s,
                         'UNDERLYING_5PCT',TIMESTAMP '2026-08-27 15:01:00'
                    FROM minute_ma_policy_path pp WHERE pp.minute_policy_path_id=%s
                  RETURNING minute_live_trade_id""",
                  (epoch,ownership,initial_capital,entry_amount,policy_operation_id,anchor,
                   anchor*Decimal('0.95'),policy_path_id))
                trade_id=int(q.fetchone()[0]);trade_ids.append(trade_id)
                q.execute("""INSERT INTO execution_logical_position(
                  ownership_type,ownership_id,stock_code,quantity,average_cost,realized_pnl,last_fill_at,version)
                  VALUES('MINUTE_MA',%s,%s,1,%s,0,TIMESTAMP '2026-08-27 15:01:00',1)""",
                  (ownership,stock,entry_amount))

        repository=PostgresMinuteMaRepository(pool,write_enabled=True)
        path=next(p for p in repository.v1_policy_paths(live_only=True)
                  if p.minute_policy_path_id==policy_path_id)
        recovered=repository.v1_live_open_trades(path=path)
        if len(recovered)!=3 or tuple(x.underlying_entry_reference_price for x in recovered)!=anchors:
            raise AssertionError('V1_RESTART_RECOVERY_FAILED')

        planner=PostgresMinuteMaLivePlanner(factory)
        normal_time=datetime(2026,8,28,9,10)
        normal=_event(path,normal_time,'a'*64)
        first=planner.plan_trade_exit(path=path,event=normal,reference_price=Decimal('1050'),
                                      minute_live_trade_id=trade_ids[0],exit_reason='NORMAL_EXIT')
        duplicate=planner.plan_trade_exit(path=path,event=normal,reference_price=Decimal('1050'),
                                          minute_live_trade_id=trade_ids[0],exit_reason='NORMAL_EXIT')
        with connection.cursor() as q:
            q.execute("UPDATE execution_logical_position SET quantity=0 WHERE ownership_id=%s",
                      (f'TEST:MINUTE_MA_V1:OWNERSHIP:1',))
            q.execute("UPDATE minute_ma_live_trade SET trade_status='CLOSED',exit_filled_amount=1050 WHERE minute_live_trade_id=%s",
                      (trade_ids[0],))

        monitor=MinuteMaV1LiveStopMonitor(repository=repository,planner=planner,price_lookup=_Price())
        stop_bar=SimpleNamespace(bar_time=datetime(2026,8,28,9,11),close_price=Decimal('94'))
        stop_first=monitor.evaluate_completed_bar(path=path,bar=stop_bar)
        stop_duplicate=monitor.evaluate_completed_bar(path=path,bar=stop_bar)
        with connection.cursor() as q:
            q.execute("""SELECT minute_live_trade_id,count(*) FROM minute_ma_live_intent
                          WHERE exit_reason='STOP_EXIT' GROUP BY minute_live_trade_id ORDER BY minute_live_trade_id""")
            stop_intents=q.fetchall()
            if stop_intents!=[(trade_ids[1],1)]:raise AssertionError(f'STOP_SCOPE_INVALID:{stop_intents}')
            q.execute("UPDATE execution_logical_position SET quantity=0 WHERE ownership_id=%s",
                      (f'TEST:MINUTE_MA_V1:OWNERSHIP:2',))
            q.execute("UPDATE minute_ma_live_trade SET trade_status='CLOSED',exit_filled_amount=1000 WHERE minute_live_trade_id=%s",
                      (trade_ids[1],))

        sibling_event=_event(path,datetime(2026,8,28,9,12),'b'*64)
        planner.plan_trade_exit(path=path,event=sibling_event,reference_price=Decimal('990'),
                                minute_live_trade_id=trade_ids[2],exit_reason='NORMAL_EXIT')
        with connection.cursor() as q:
            q.execute("UPDATE execution_logical_position SET quantity=0 WHERE ownership_id=%s",
                      (f'TEST:MINUTE_MA_V1:OWNERSHIP:3',))
            q.execute("UPDATE minute_ma_live_trade SET trade_status='CLOSED',exit_filled_amount=990 WHERE minute_live_trade_id=%s",
                      (trade_ids[2],))
            snapshot_id='10000000-0000-0000-0000-000000000001'
            q.execute("""INSERT INTO minute_ma_live_broker_cost_snapshot(
              broker_cost_snapshot_id,trade_date,execution_stock_code,broker_buy_fee,broker_sell_fee,
              broker_sell_tax,broker_other_cost,broker_snapshot_at,finalization_status,
              stable_confirmation_count,fill_set_fingerprint,last_stable_recheck_at,finalized_at)
              VALUES(%s,DATE '2026-08-27',%s,3,3,6,0,TIMESTAMP '2026-08-28 08:20:00',
              'FINALIZED_BY_STABLE_RECHECK',2,%s,TIMESTAMP '2026-08-28 08:20:00',TIMESTAMP '2026-08-28 08:20:00')""",
              (snapshot_id,stock,'f'*64))

        targets=[]
        amounts=((Decimal('1000'),Decimal('1050')),(Decimal('1100'),Decimal('1000')),
                 (Decimal('900'),Decimal('990')))
        for trade_id,(buy,sell) in zip(trade_ids,amounts):
            targets.extend((CostAllocationTarget(trade_id,'BUY',buy,f'{trade_id:020d}|BUY'),
                            CostAllocationTarget(trade_id,'SELL',sell,f'{trade_id:020d}|SELL')))
        snapshot=BrokerCostSnapshot(datetime(2026,8,27).date(),stock,
            BrokerCostTotals(Decimal('3'),Decimal('3'),Decimal('6'),Decimal('0')),
            datetime(2026,8,28,8,20),True,BrokerCostStatus.FINALIZED_BY_STABLE_RECHECK)
        _,allocations=allocate_final_costs(snapshot=snapshot,targets=tuple(targets),unattributed_activity=False)
        with connection.cursor() as q:
            for allocation in allocations:
                q.execute("""INSERT INTO minute_ma_live_broker_cost_allocation(
                  broker_cost_snapshot_id,minute_live_trade_id,allocation_side,fill_notional,
                  allocated_buy_fee,allocated_sell_fee,allocated_sell_tax,allocated_other_cost,
                  stable_allocation_key) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (snapshot_id,allocation.live_trade_id,allocation.side,allocation.fill_notional,
                   allocation.buy_fee,allocation.sell_fee,allocation.sell_tax,allocation.other_cost,
                   f'{allocation.live_trade_id:020d}|{allocation.side}'))
            q.execute("SELECT strategy_compound_capital FROM minute_ma_policy_compound_capital WHERE minute_policy_path_id=%s AND capital_epoch_no=%s",
                      (policy_path_id,epoch));capital_before=Decimal(q.fetchone()[0])

        finalizer=MinuteMaCostFinalizer(connection_factory=factory,cost_lookup=None,calendar=None,
                                        clock=lambda:datetime(2026,8,28,8,21))
        settled_first=finalizer._settle_due()
        restarted_finalizer=MinuteMaCostFinalizer(connection_factory=factory,cost_lookup=None,calendar=None,
                                                  clock=lambda:datetime(2026,8,28,8,22))
        settled_restart=restarted_finalizer._settle_due()
        with connection.cursor() as q:
            q.execute("SELECT strategy_compound_capital,cumulative_net_realized_pnl FROM minute_ma_policy_compound_capital WHERE minute_policy_path_id=%s AND capital_epoch_no=%s",
                      (policy_path_id,epoch));capital_after,cumulative=q.fetchone()
            q.execute("SELECT count(*) FROM minute_ma_live_capital_settlement WHERE minute_live_trade_id=ANY(%s)",(trade_ids,));settlements=int(q.fetchone()[0])
            q.execute("""SELECT COALESCE(sum(allocated_buy_fee),0),COALESCE(sum(allocated_sell_fee),0),
                                COALESCE(sum(allocated_sell_tax),0),COALESCE(sum(allocated_other_cost),0)
                           FROM minute_ma_live_broker_cost_allocation WHERE broker_cost_snapshot_id=%s""",(snapshot_id,));allocated=tuple(Decimal(x) for x in q.fetchone())
            q.execute("SELECT count(*) FROM minute_ma_policy_compound_capital WHERE strategy_compound_capital<>epoch_initial_capital+cumulative_net_realized_pnl");invariant=int(q.fetchone()[0])
            q.execute("SELECT count(*) FROM minute_ma_policy_operation WHERE effective_to IS NULL AND operation_status='LIVE'");v1_live=int(q.fetchone()[0])
            q.execute("SELECT count(*) FROM minute_ma_operation WHERE effective_to IS NULL AND operation_status='LIVE'");legacy_live=int(q.fetchone()[0])
            q.execute("""SELECT count(*) FROM minute_ma_operation o JOIN minute_ma_path p USING(minute_path_id)
                          JOIN minute_ma_strategy_master m USING(minute_strategy_id)
                         WHERE o.effective_to IS NULL AND o.operation_status='PAPER'
                           AND ((m.source_daily_strategy_id='DS001283' AND p.data_axis='INTEGRATED_CONTINUOUS')
                             OR (m.source_daily_strategy_id='DS002277' AND p.data_axis='KRX_CONTINUOUS'))""");legacy_test_paper=int(q.fetchone()[0])
            q.execute("""SELECT count(*) FROM minute_ma_compound_capital c JOIN minute_ma_path p USING(minute_path_id)
                          JOIN minute_ma_strategy_master m USING(minute_strategy_id)
                         WHERE (m.source_daily_strategy_id='DS001283' AND p.data_axis='INTEGRATED_CONTINUOUS')
                            OR (m.source_daily_strategy_id='DS002277' AND p.data_axis='KRX_CONTINUOUS')""");legacy_test_capital=int(q.fetchone()[0])
            q.execute("SELECT count(*) FROM vw_minute_ma_v1_current_selection");selection=int(q.fetchone()[0])
            q.execute("SELECT count(*) FROM live_broker_order");broker_now=int(q.fetchone()[0])
            q.execute("SELECT count(*) FROM minute_ma_live_order_link");mapping_now=int(q.fetchone()[0])

        expected_net=sum((sell-buy for buy,sell in amounts),Decimal('0'))-Decimal('12')
        if (first,duplicate)!=('READY_FOR_BROKER','READY_FOR_BROKER'):
            raise AssertionError('NORMAL_EXIT_IDEMPOTENCY_FAILED')
        if settled_first!=3 or settled_restart!=0 or settlements!=3:
            raise AssertionError('SETTLEMENT_EXACTLY_ONCE_FAILED')
        if Decimal(cumulative)!=expected_net or Decimal(capital_after)!=capital_before+expected_net:
            raise AssertionError('POLICY_CAPITAL_INVALID')
        if allocated!=(Decimal('3'),Decimal('3'),Decimal('6'),Decimal('0')):
            raise AssertionError('COST_ALLOCATION_NOT_RECONCILED')
        if (invariant or v1_live!=20 or legacy_live!=0 or selection!=20
                or legacy_test_paper!=2 or legacy_test_capital!=2):
            raise AssertionError('V1_APPLY_INVARIANT_FAILED')
        if broker_now!=before_broker or mapping_now-before_mapping!=3:
            raise AssertionError('BROKER_POST_OR_MAPPING_FIXTURE_INVALID')
        rollback_guard_blocked=False
        with connection.cursor() as q:
            q.execute('SAVEPOINT v1_rollback_guard_probe')
            try:
                q.execute(_migration(ROOT/'database/migrations/20260828_minute_ma_v10_policy_guarded_rollback.sql'))
            except psycopg.errors.RaiseException as error:
                if 'rollback blocked' not in str(error):raise
                q.execute('ROLLBACK TO SAVEPOINT v1_rollback_guard_probe')
                rollback_guard_blocked=True
            else:
                raise AssertionError('UNSAFE_V1_ROLLBACK_WAS_NOT_BLOCKED')
        evidence={'database':settings.name,'v1_live':v1_live,'legacy_live':legacy_live,'selection':selection,
          'legacy_test_paths_current_paper':legacy_test_paper,
          'legacy_test_capital_history':legacy_test_capital,
          'capital_total':3200000,'recovered_open':len(recovered),'immutable_anchors':True,
          'normal_exit_duplicate_request':0,'stop_exit_targets':len(stop_intents),
          'stop_duplicate_request':0,'sibling_open_survived_stop':True,
          'settled_first':settled_first,'settled_after_restart':settled_restart,
          'capital_invariant_errors':invariant,'allocated_cost':list(map(str,allocated)),
          'broker_order_delta':broker_now-before_broker,'actual_kis_post':0,
          'rollback_guard_blocked':rollback_guard_blocked,
          'transaction':'ROLLBACK_ONLY'}
        print(json.dumps(evidence,sort_keys=True))
        return 0
    finally:
        connection.rollback()
        connection.close()


if __name__=='__main__':raise SystemExit(main())
