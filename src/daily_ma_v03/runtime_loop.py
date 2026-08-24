"""Injectable Daily MA orchestration loop; systemd entrypoint calls this."""
from __future__ import annotations
from datetime import date

class DailyMaActualRuntimeLoop:
 def __init__(self,*,request_repository,orchestrator,checkpoint_poller,cost_finalizer):
  self.request_repository=request_repository;self.orchestrator=orchestrator;self.checkpoint_poller=checkpoint_poller;self.cost_finalizer=cost_finalizer
 def run_once(self,*,today:date):
  submitted=[]
  for request_key in self.request_repository.discover_ready_request_keys():
   _,status=self.orchestrator.process_request(request_key);submitted.append((request_key,status))
  # Polling is lookup-only; UNKNOWN recovery and checkpoint deltas are owned
  # by this injected component and cannot cause a second submit.
  checkpoint=self.checkpoint_poller.poll_and_recover(today=today)
  finalized=self.cost_finalizer.finalize_due(today=today)
  return {'submitted':tuple(submitted),'checkpoint':checkpoint,'finalized':finalized}
