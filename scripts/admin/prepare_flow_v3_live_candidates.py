"""FLOW operation admin. Default list is read-only; never enables global SEND."""
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def main():
    from dotenv import load_dotenv
    from src.repository.database import DatabaseSettings,create_connection_pool
    from src.flow_v3.live_operations import LiveOperations
    from src.flow_v3.live_broker import kst_now
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command')
    sub.add_parser('list')
    find=sub.add_parser('strategies')
    find.add_argument('--search',default='')
    find.add_argument('--limit',type=int,default=50)
    add=sub.add_parser('register')
    add.add_argument('--strategy',required=True)
    add.add_argument('--route',choices=('UNDERLYING','LEVERAGE','INVERSE'),required=True)
    add.add_argument('--capital',required=True)
    add.add_argument('--approval-reference',required=True)
    for name in ('start-entry','stop-entry','close'):
        cmd=sub.add_parser(name)
        cmd.add_argument('--operation',type=int,required=True)
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    pool=create_connection_pool(DatabaseSettings.from_environment())
    try:
        ops=LiveOperations(pool)
        if args.command=='register':
            result={'operation_id':ops.register(args.strategy,args.route,args.capital,args.approval_reference,kst_now()),
                    'entry_enabled':False,'global_send_changed':False}
        elif args.command in ('start-entry','stop-entry','close'):
            ops.set_entry(args.operation,args.command=='start-entry',kst_now(),end=args.command=='close')
            result={'operation_id':args.operation,'action':args.command,'global_send_changed':False}
        elif args.command=='strategies':
            from psycopg.rows import dict_row
            with pool.connection() as c,c.transaction(),c.cursor(row_factory=dict_row) as q:
                q.execute('SET TRANSACTION READ ONLY')
                q.execute('SELECT * FROM flow_v3_strategy_master WHERE strategy_id ILIKE %s ORDER BY strategy_id LIMIT %s',
                          ('%'+args.search+'%',max(1,min(100,args.limit))))
                result=q.fetchall()
        else:
            result=ops.listing()
        print(json.dumps(result,default=str,ensure_ascii=False))
    finally:
        pool.close()


if __name__=='__main__':
    main()
