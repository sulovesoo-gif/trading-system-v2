"""Read-only Forward path/candidate cardinality audit."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
def main():
 import psycopg
 load_dotenv(ROOT/'.env')
 queries={'master_rows':'select count(*) from research_strategy_master','core_resolved_rows':"select count(*) from research_strategy_master where enabled_research_yn='Y'",'active_candidates':"select count(*) from forward_candidate where active_yn='Y'",'active_paths':"select count(*) from forward_execution_path where active_yn='Y'",'paths_with_multiple_candidates':"select count(*) from (select forward_execution_id from forward_candidate where active_yn='Y' group by forward_execution_id having count(*)>1) x"}
 with psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs()) as c,c.cursor() as q:
  for key,sql in queries.items():q.execute(sql);print(key+'='+str(q.fetchone()[0]))
if __name__=='__main__':main()
