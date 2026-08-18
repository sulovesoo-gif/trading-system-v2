"""Forward observation service: completed RAW -> frozen Core -> one-share no-send plan."""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.collector.raw.converters import kst_now
from src.forward.persistence import PostgresForwardRegistry
from src.forward.definition_registry import ForwardDefinitionRegistry
from src.forward.raw_provider import PostgresCompletedMinuteProvider
from src.forward.planning import PostgresForwardNoSendPlanner
from src.repository.database import DatabaseSettings,create_connection_pool
from src.strategy_core import StrategyCore
from src.research_core import ResearchMasterCore

def main():
 p=argparse.ArgumentParser();p.add_argument('--interval-seconds',type=int,default=60);p.add_argument('--once',action='store_true');a=p.parse_args()
 load_dotenv(ROOT/'.env'); pool=create_connection_pool(DatabaseSettings.from_environment())
 try:
  factory=pool.connection; candidates=PostgresForwardRegistry(factory); definitions=ForwardDefinitionRegistry(factory); raw=PostgresCompletedMinuteProvider(pool); research_raw=PostgresCompletedMinuteProvider(pool,venue='INTEGRATED'); planner=PostgresForwardNoSendPlanner(factory); research=ResearchMasterCore()
  while True:
   now=kst_now().replace(second=0,microsecond=0); planned=0
   for candidate in candidates.active_candidates():
    instance,definition=definitions.resolve(candidate.strategy_reference); provider=research_raw if candidate.strategy_reference.startswith('RESEARCH_STRATEGY_') else raw; source=provider.bars(definition.signal_stock_code,now.date().isoformat())
    decisions=research.entries(definition,source) if candidate.strategy_reference.startswith('RESEARCH_STRATEGY_') else StrategyCore(definition).entry_decisions(source)
    for decision in decisions:
     if decision.target_time != now: continue
     bar=provider.bar_at(definition.execution_stock_code,now)
     if bar is not None and planner.plan(candidate_id=candidate.candidate_id,strategy_instance_id=instance,execution_stock_code=definition.execution_stock_code,source_decision_id=decision.decision_id,target_time=now,reference_price=bar.open): planned+=1
   print({'candidates':len(candidates.active_candidates()),'planned':planned,'broker_send_eligible':False},flush=True)
   if a.once:return 0
   time.sleep(max(1,a.interval_seconds))
 finally: pool.close()
if __name__=='__main__': raise SystemExit(main())
