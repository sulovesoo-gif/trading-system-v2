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
else: raise SystemExit('Daily MA real transport requires explicit SEND authorization')
