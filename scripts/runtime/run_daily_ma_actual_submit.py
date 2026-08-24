"""Daily MA actual-runtime entrypoint; fake mode is the TEST systemd E2E lane."""
from __future__ import annotations
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
if os.getenv('DAILY_MA_ACTUAL_SEND','N') != 'Y': print('Daily MA actual submit runtime started: SEND_LOCKED')
elif os.getenv('DAILY_MA_RUNTIME_TRANSPORT','') == 'FAKE':
 from datetime import date
 from src.daily_ma_v03.runtime_loop import DailyMaActualRuntimeLoop
 from src.daily_ma_v03.send_orchestration import DailyMaSendOrchestrator
 class Store:
  def __init__(self):self.claimed=False
  def discover_ready_request_keys(self):return ('SYSTEMD_FAKE',) if not self.claimed else ()
  def claim(self,request_key):
   if self.claimed:return None
   self.claimed=True;return type('Order',(),{'client_order_key':request_key})()
  def acknowledge(self,**_):pass
  def mark_unknown(self,**_):pass
 class Runtime:
  def submit(self,o):return type('Record',(),{'broker_order_number':'FAKE'})(),'ACK'
 class Poll:
  def poll_and_recover(self,**_):return 'READ_ONLY'
 class Cost:
  def finalize_due(self,**_):return 'PENDING'
 store=Store();loop=DailyMaActualRuntimeLoop(request_repository=store,orchestrator=DailyMaSendOrchestrator(submit_store=store,submit_runtime=Runtime()),checkpoint_poller=Poll(),cost_finalizer=Cost())
 print('Daily MA fake orchestration='+str(loop.run_once(today=date.today())))
elif os.getenv('DAILY_MA_RUNTIME_TRANSPORT','') == 'PRODUCTION_GUARDED':
 from datetime import date
 from dotenv import load_dotenv
 from src.repository.database import DatabaseSettings
 from src.daily_ma_v03.actual_submit_repository import PostgresDailyMaActualSubmitStore
 from src.daily_ma_v03.production_polling import ProductionCheckpointPoller,ProductionCostFinalizer
 from src.daily_ma_v03.fill_checkpoint import PostgresDailyMaFillCheckpointStore
 from src.daily_ma_v03.kis_order_history import DailyMaKISOrderHistoryLookup
 from src.daily_ma_v03.kis_cost_history import DailyMaKISProductDayCostLookup
 from src.daily_ma_v03.broker_cost_repository import PostgresDailyMaBrokerCostStore
 from src.daily_ma_v03.capital_repository import PostgresDailyMaCapitalStore
 from src.daily_ma_v03.settlement_coordinator import DailyMaSettlementCoordinator
 from src.collector.raw.kis_client import KISClient
 from src.collector.raw.kis_order_account import KISOrderAccount
 from src.collector.raw.domestic_stock.holiday_calendar_collector import HolidayCalendarCollector
 from src.service.kis_trading_calendar import KisTradingCalendar
 from src.daily_ma_v03.runtime_loop import DailyMaActualRuntimeLoop
 from src.daily_ma_v03.send_orchestration import DailyMaSendOrchestrator
 import psycopg
 load_dotenv(Path(__file__).resolve().parents[2]/'.env'); settings=DatabaseSettings.from_environment()
 class GuardedRuntime:
  def submit(self,o): return type('Record',(),{'broker_order_number':'GUARDED_NO_POST'})(),'ACK'
 factory=lambda:psycopg.connect(**settings.connection_kwargs())
 store=PostgresDailyMaActualSubmitStore(factory)
 client=KISClient(); account=KISOrderAccount.from_environment()
 poller=ProductionCheckpointPoller(repository=store,history_lookup=DailyMaKISOrderHistoryLookup(client=client,account=account),checkpoint_store=PostgresDailyMaFillCheckpointStore(factory))
 costs=PostgresDailyMaBrokerCostStore(factory)
 settlement=DailyMaSettlementCoordinator(repository=costs,capital_store=PostgresDailyMaCapitalStore(factory))
 finalizer=ProductionCostFinalizer(connection_factory=factory,cost_lookup=DailyMaKISProductDayCostLookup(client=client,account=account),cost_store=costs,calendar=KisTradingCalendar(HolidayCalendarCollector(client)),settlement_coordinator=settlement)
 loop=DailyMaActualRuntimeLoop(request_repository=store,orchestrator=DailyMaSendOrchestrator(submit_store=store,submit_runtime=GuardedRuntime()),checkpoint_poller=poller,cost_finalizer=finalizer)
 print('Daily MA production guarded orchestration='+str(loop.run_once(today=date.today())))
else: raise SystemExit('Daily MA real transport requires explicit SEND authorization')
