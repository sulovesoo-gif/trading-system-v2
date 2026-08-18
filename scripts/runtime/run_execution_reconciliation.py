"""Read-only broker position reconciliation service; no order transport import."""
from __future__ import annotations
import argparse,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.broker.position_lookup import KISBrokerPositionLookup
from src.collector.raw.kis_client import KISClient
from src.collector.raw.kis_order_account import KISOrderAccount
from src.execution.persistence import PostgresOwnershipStore
from src.execution.reconciliation import ExecutionReconciliationService
from src.repository.database import DatabaseSettings,create_connection_pool

def main():
 p=argparse.ArgumentParser();p.add_argument('--interval-seconds',type=int,default=300);p.add_argument('--once',action='store_true');a=p.parse_args()
 load_dotenv(ROOT/'.env');pool=create_connection_pool(DatabaseSettings.from_environment())
 try:
  lookup=KISBrokerPositionLookup(client=KISClient(),account=KISOrderAccount.from_environment())
  service=ExecutionReconciliationService(ownership_store=PostgresOwnershipStore(pool.connection),broker_lookup=lookup)
  while True:
   result=service.reconcile();print({'stocks':len(result),'statuses':[x.status for x in result]},flush=True)
   if a.once:return 0
   time.sleep(max(1,a.interval_seconds))
 finally:pool.close()
if __name__=='__main__':raise SystemExit(main())
