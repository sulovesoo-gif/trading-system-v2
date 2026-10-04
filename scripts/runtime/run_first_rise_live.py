"""FIRST_RISE actual execution only. Installation/start require operator approval.

This script never initializes activation and never submits from the FLOW process.
"""
import argparse
import json
import logging
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def main():
    import psycopg
    from dotenv import load_dotenv
    from src.repository.database import DatabaseSettings,create_connection_pool
    from src.collector.raw.kis_client import KISClient
    from src.collector.raw.kis_order_account import KISOrderAccount
    from src.broker.cash_lookup import KISBrokerAvailableCashLookup
    from src.broker.shared_cost_repository import SharedBrokerCostFinalizer
    from src.daily_ma_v03.actual_submit import DailyMaBrokerSubmitRuntime,InMemoryDailyMaSubmitStore
    from src.daily_ma_v03.send_orchestration import DailyMaSendOrchestrator
    from src.daily_ma_v03.kis_order_history import DailyMaKISOrderHistoryLookup
    from src.daily_ma_v03.kis_cost_history import DailyMaKISProductDayCostLookup
    from src.collector.raw.domestic_stock.holiday_calendar_collector import HolidayCalendarCollector
    from src.service.kis_trading_calendar import KisTradingCalendar
    from src.minute_ma.reference_price import MinuteMaKISReferencePriceLookup
    from src.first_rise_breakout.j_epoch import DailyCapitalContext,JCapitalEpochRepository
    from src.first_rise_breakout.j_live_repository import JLiveRepository
    from src.first_rise_breakout.j_live_runtime import JLiveRuntime
    from src.first_rise_breakout.j_submit import JSubmitStore,JKISOrderTransport
    from src.first_rise_breakout.j_recovery import JRecovery
    from src.first_rise_breakout.j_cancel import JCancelRuntime,KRXExecutionSession
    parser=argparse.ArgumentParser()
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--cycle-seconds',type=float,default=60)
    args=parser.parse_args()
    if args.cycle_seconds<=0:parser.error('cycle-seconds must be positive')
    load_dotenv(ROOT/'.env')
    logging.basicConfig(level=logging.INFO)
    settings=DatabaseSettings.from_environment()
    factory=lambda:psycopg.connect(**settings.connection_kwargs())
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
    # Process exclusivity, not an ENTRY activation gate. Crash releases the lock.
    with factory() as lease:
        if not lease.execute("SELECT pg_try_advisory_lock(hashtext('first_rise_j_execution_process'))").fetchone()[0]:
            raise RuntimeError('FIRST_RISE_EXECUTION_ALREADY_RUNNING')
        lease.commit()
        pool=create_connection_pool(settings)
        try:
            client=KISClient();account=KISOrderAccount.from_environment()
            context=DailyCapitalContext(JCapitalEpochRepository(pool))
            calendar=KisTradingCalendar(HolidayCalendarCollector(client))
            session=KRXExecutionSession(calendar)
            store=JSubmitStore(factory,config_provider=lambda:context.config,session_open=session)
            transport=JKISOrderTransport(client=client,account=account,attempt_recorder=store)
            from src.first_rise_breakout.v2_capacity_repository import CapacityMonitor
            from src.service.ntfy_alert_service import NtfyAlertService,NtfySettings
            from src.service.email_alert_service import EmailAlertService,EmailSettings
            notifier=None
            try:notifier=NtfyAlertService(NtfySettings.from_environment())
            except RuntimeError:
                try:notifier=EmailAlertService(EmailSettings.from_environment())
                except RuntimeError:logging.getLogger(__name__).info('FIRST_RISE_CAPACITY_ALERT_LOG_ONLY')
            submitter=DailyMaSendOrchestrator(submit_store=store,submit_runtime=DailyMaBrokerSubmitRuntime(
                store=InMemoryDailyMaSubmitStore(),transport=transport,profile=None))
            runtime=JLiveRuntime(context=context,planner=JLiveRepository(pool),submit_store=store,
                submitter=submitter,recovery=JRecovery(pool,DailyMaKISOrderHistoryLookup(client=client,account=account)),
                price_lookup=MinuteMaKISReferencePriceLookup(client),cash_lookup=KISBrokerAvailableCashLookup(client=client,account=account),
                cancellations=JCancelRuntime(pool,client,account,session_open=session),
                capacity_monitor=CapacityMonitor(pool,notifier),
                cost_finalizer=SharedBrokerCostFinalizer(connection_factory=factory,
                    cost_lookup=DailyMaKISProductDayCostLookup(client=client,account=account),
                    calendar=calendar))
            while not stop.is_set():
                result=runtime.cycle(at=datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None))
                print(json.dumps(result,default=str),flush=True)
                if args.once:break
                stop.wait(args.cycle_seconds)
        finally:pool.close()


if __name__=='__main__':main()
