"""Explicit operator CLI for Forward candidates; it never submits orders."""
from __future__ import annotations
import argparse, hashlib, sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.forward import ForwardCandidate,ForwardExecutionPath,PostgresForwardRegistry
from src.repository.database import DatabaseSettings,create_connection_pool

def candidate_id(args):
 material='|'.join((args.strategy_instance_id,args.entry_identity,args.exit_identity,args.execution_stock_code))
 return 'FORWARD_CANDIDATE_'+hashlib.sha256(material.encode()).hexdigest()[:20]
def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 create=sub.add_parser('create');
 for name in ('strategy_instance_id','signal_stock_code','execution_stock_code','entry_identity','exit_identity','selection_reason','approved_by'): create.add_argument('--'+name.replace('_','-'),required=True)
 create.add_argument('--activate',action='store_true')
 for name in ('activate','deactivate'):
  q=sub.add_parser(name);q.add_argument('candidate_id')
 sub.add_parser('list');a=p.parse_args();load_dotenv(ROOT/'.env');pool=create_connection_pool(DatabaseSettings.from_environment())
 try:
  registry=PostgresForwardRegistry(pool.connection)
  if a.command=='list':
   print([x.__dict__ for x in registry.active_candidates()]);return 0
  if a.command=='create':
   c=ForwardCandidate(candidate_id(a),a.strategy_instance_id,ForwardExecutionPath(a.entry_identity,a.exit_identity,a.execution_stock_code),a.signal_stock_code,a.selection_reason,datetime.now(),a.approved_by,a.activate)
   registry.register(c);print({'forward_candidate_id':c.candidate_id,'active':c.active,'broker_send_eligible':False});return 0
  with pool.connection() as connection,connection.cursor() as cursor:
   cursor.execute("UPDATE forward_candidate SET active_yn=%s WHERE forward_candidate_id=%s RETURNING forward_candidate_id",('Y' if a.command=='activate' else 'N',a.candidate_id))
   if cursor.fetchone() is None:raise ValueError('candidate not found')
   connection.commit()
  print({'forward_candidate_id':a.candidate_id,'active':a.command=='activate','broker_send_eligible':False});return 0
 finally:pool.close()
if __name__=='__main__':raise SystemExit(main())
