"""Minute MA production orchestration. Missing authorization is fail-closed."""
from __future__ import annotations
import json,sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
import psycopg
from src.repository.database import DatabaseSettings,create_connection_pool
from src.collector.raw.kis_client import KISClient
from src.collector.raw.kis_order_account import KISOrderAccount
from src.broker.cash_lookup import KISBrokerAvailableCashLookup
from src.daily_ma_v03.actual_submit import DailyMaBrokerSubmitRuntime,InMemoryDailyMaSubmitStore
from src.daily_ma_v03.send_orchestration import DailyMaSendOrchestrator
from src.daily_ma_v03.kis_order_history import DailyMaKISOrderHistoryLookup
from src.daily_ma_v03.kis_cost_history import DailyMaKISProductDayCostLookup
from src.collector.raw.domestic_stock.holiday_calendar_collector import HolidayCalendarCollector
from src.service.kis_trading_calendar import KisTradingCalendar
from src.minute_ma.repository import PostgresMinuteMaRepository
from src.minute_ma.live_planner import PostgresMinuteMaLivePlanner
from src.minute_ma.v1_live_runtime import MinuteMaV1LiveRuntime
from src.minute_ma.v1_live_nosend import MinuteMaV1LiveNoSendRuntime
from src.minute_ma.live_nosend import PostgresMinuteMaNoSendAdapter
from src.minute_ma.reference_price import MinuteMaKISReferencePriceLookup
from src.minute_ma.send_authorization import MinuteMaSendProfile
from src.minute_ma.kis_order_transport import MinuteMaKISOrderTransport,MinuteMaKISOrderTransportConfig
from src.minute_ma.actual_submit_repository import PostgresMinuteMaActualSubmitStore
from src.minute_ma.fill_checkpoint import PostgresMinuteMaFillCheckpointStore
from src.minute_ma.production_polling import MinuteMaCheckpointPoller
from src.minute_ma.cost_finalizer import MinuteMaCostFinalizer

def main():
    load_dotenv(ROOT/'.env');profile=MinuteMaSendProfile.from_environment()
    if profile.environment_value not in (None,'N','Y'):
        raise SystemExit('MINUTE_MA_SEND_INVALID_FAIL_CLOSED')
    settings=DatabaseSettings.from_environment();factory=lambda:psycopg.connect(**settings.connection_kwargs())
    with factory() as c,c.cursor() as q:
        q.execute("SELECT send_enabled FROM minute_ma_send_profile WHERE profile_code='MINUTE_MA_LIVE_SEND'")
        db_send=q.fetchone()
        if db_send not in (("Y",),("N",)):raise SystemExit('MINUTE_MA_DB_SEND_PROFILE_INVALID')
        if (profile.enabled and db_send!=("Y",)) or (not profile.enabled and db_send!=("N",)):
            raise SystemExit('MINUTE_MA_SEND_AUTHORIZATION_MISMATCH')
        q.execute("""SELECT DISTINCT s.execution_code FROM minute_ma_policy_operation o
          JOIN minute_ma_policy_path pp USING(minute_policy_path_id)
          JOIN minute_ma_path p USING(minute_path_id) JOIN minute_ma_strategy_master s USING(minute_strategy_id)
          WHERE o.effective_to IS NULL AND o.operation_status='LIVE'""")
        whitelist=frozenset(str(x[0]) for x in q.fetchall())
    client=KISClient();account=KISOrderAccount.from_environment();pool=create_connection_pool(settings)
    now=datetime.now(ZoneInfo('Asia/Seoul'));today=now.date()
    try:
        planner=PostgresMinuteMaLivePlanner(factory)
        repository=PostgresMinuteMaRepository(pool,write_enabled=True)
        price_lookup=MinuteMaKISReferencePriceLookup(client)
        cash_lookup=KISBrokerAvailableCashLookup(client=client,account=account)
        if not profile.enabled:
            signals=MinuteMaV1LiveNoSendRuntime(repository=repository,
              adapter=PostgresMinuteMaNoSendAdapter(factory),execution_price_lookup=price_lookup,
              underlying_price_lookup=price_lookup,cash_lookup=cash_lookup)
            signal_result=signals.run_day(trading_date=today)
            print(json.dumps({'mode':'V1_LIVE_NOSEND','signals':signal_result,
                              'actual_post_count':0},default=str,sort_keys=True))
            return 0
        signals=MinuteMaV1LiveRuntime(repository=repository,planner=planner,
          price_lookup=price_lookup,cash_lookup=cash_lookup)
        signal_result=signals.run_day(trading_date=today)
        store=PostgresMinuteMaActualSubmitStore(factory)
        transport=MinuteMaKISOrderTransport(client=client,config=MinuteMaKISOrderTransportConfig.from_environment(whitelist=whitelist))
        runtime=DailyMaBrokerSubmitRuntime(store=InMemoryDailyMaSubmitStore(),transport=transport,profile=profile)
        submitter=DailyMaSendOrchestrator(submit_store=store,submit_runtime=runtime)
        submitted={}
        def submit_ready():
            for key in store.discover_ready_request_keys():
                _,status=submitter.process_request(key);submitted[status]=submitted.get(status,0)+1
        submit_ready()
        poll=MinuteMaCheckpointPoller(repository=store,history_lookup=DailyMaKISOrderHistoryLookup(client=client,account=account),checkpoint_store=PostgresMinuteMaFillCheckpointStore(factory)).poll()
        submit_ready()
        costs=MinuteMaCostFinalizer(connection_factory=factory,cost_lookup=DailyMaKISProductDayCostLookup(client=client,account=account),
          calendar=KisTradingCalendar(HolidayCalendarCollector(client)),clock=lambda:datetime.now(ZoneInfo('Asia/Seoul'))).finalize_due(today=today)
        print(json.dumps({'mode':'REAL_V1','signals':signal_result,'submitted':submitted,'poll':poll,'costs':costs,
                          'actual_post_count':transport.actual_post_send_count},default=str,sort_keys=True))
    finally:pool.close()
    return 0
if __name__=='__main__':raise SystemExit(main())
