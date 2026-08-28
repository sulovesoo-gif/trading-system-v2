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
   broker=lookup.net_quantities()
   # Zero-position execution products still need a fresh HEALTHY/PASS audit;
   # absence from KIS holdings is not an UNKNOWN state.
   with pool.connection() as c,c.cursor() as q:
    q.execute("""SELECT DISTINCT execution_code FROM daily_strategy_master m JOIN daily_strategy_operation o USING(strategy_id)
      WHERE o.effective_to IS NULL AND o.operation_status='LIVE'
      UNION SELECT DISTINCT s.execution_code FROM minute_ma_operation o JOIN minute_ma_path p USING(minute_path_id)
      JOIN minute_ma_strategy_master s USING(minute_strategy_id)
      WHERE o.effective_to IS NULL AND o.operation_status='LIVE'
      UNION SELECT DISTINCT s.execution_code FROM minute_ma_policy_operation o
      JOIN minute_ma_policy_path pp USING(minute_policy_path_id)
      JOIN minute_ma_path p USING(minute_path_id)
      JOIN minute_ma_strategy_master s USING(minute_strategy_id)
      WHERE o.effective_to IS NULL AND o.operation_status='LIVE'""")
    for (stock,) in q.fetchall():broker.setdefault(str(stock),0)
   result=service.ownership_store.reconcile(broker);print({'stocks':len(result),'statuses':[x.status for x in result]},flush=True)
   if a.once:return 0
   time.sleep(max(1,a.interval_seconds))
 finally:pool.close()
if __name__=='__main__':raise SystemExit(main())
