"""Build (and only with --apply, activate) deduplicated Research Forward paths."""
from __future__ import annotations
import argparse, sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings,create_connection_pool
from src.research_core.registry import PostgresResearchMasterRegistry
from src.research_core.fingerprint import deduplicate
from src.forward.contracts import ForwardCandidate,ForwardExecutionPath
from src.forward.persistence import PostgresForwardRegistry

def main() -> int:
 p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args();load_dotenv(ROOT/'.env');pool=create_connection_pool(DatabaseSettings.from_environment())
 try:
  paths=deduplicate(PostgresResearchMasterRegistry(pool).definitions())
  result={'research_master_rows':802,'unique_execution_paths':len(paths),'live_equivalent_count':sum(x.live_equivalent for x in paths),'apply':a.apply}
  if a.apply:
   registry=PostgresForwardRegistry(pool.connection)
   for path in paths:
    fp=ForwardExecutionPath(path.entry_identity,path.exit_identity,path.execution_stock_code)
    if path.live_equivalent:
     registry.deactivate_research_path(fp)
     continue
    candidate=ForwardCandidate('FORWARD_CANDIDATE_'+fp.path_id.removeprefix('FORWARD_'),f'RESEARCH_STRATEGY_{path.strategy_ids[0]}',fp,path.signal_stock_code,'RESEARCH_EXACT_REPLAY_CANONICAL',datetime.now(),'UNATTENDED_FORWARD_BOOTSTRAP',True)
    try: registry.register(candidate)
    except Exception as error:
     if 'duplicate key' not in str(error): raise
   result['active_forward_candidate_count']=len(paths)-sum(x.live_equivalent for x in paths)
  print(result);return 0
 finally:pool.close()
if __name__=='__main__':raise SystemExit(main())
