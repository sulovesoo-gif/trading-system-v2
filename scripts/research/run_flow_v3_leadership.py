"""Offline batch; explicit separate DSNs, no .env trading-account fallback."""
import argparse
import json
import os
import sys
from datetime import date,datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import psycopg
from src.flow_v3_leadership.snapshot import compute,publish
from src.flow_v3_leadership.source import session


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--date',type=date.fromisoformat,default=None)
    parser.add_argument('--research-start',type=date.fromisoformat,default=None)
    parser.add_argument('--write-snapshot',action='store_true')
    args=parser.parse_args()
    read=os.environ.get('LEADERSHIP_READ_DSN')
    if not read: parser.error('LEADERSHIP_READ_DSN required')
    with psycopg.connect(read) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='120s'")
        now=datetime.now(ZoneInfo('Asia/Seoul')).replace(tzinfo=None)
        asof=args.date
        if asof is None:
            # Persistent timer after downtime must choose the last completed source date.
            asof=conn.execute("""SELECT max(bar_time::date) FROM raw_stock_minute
                WHERE data_source='KIS' AND trading_venue='KRX' AND collect_cycle='1MIN'
                AND stock_code IN ('000660','005930') AND bar_time::date<=%s""",
                (now.date() if now.time()>session(conn).extended_end else now.date()-timedelta(days=1),)).fetchone()[0]
        if asof is None or asof>now.date() or (asof==now.date() and now.time()<=session(conn).extended_end):
            parser.error('Required completed extended session not available')
        start=args.research_start or conn.execute("""SELECT min(t.entry_signal_time)::date
            FROM flow_v3_paper_trade t JOIN flow_v3_strategy_master m USING(strategy_id)
            WHERE m.direction='LONG' AND m.is_enabled='Y' AND m.stock_code IN ('000660','005930')""").fetchone()[0]
        if start is None: parser.error('No source history')
        rows,audit=compute(conn,asof,start)
    result=dict(status='DRY_RUN',snapshot_date=asof,rows=len(rows),source_audit=audit)
    if args.write_snapshot:
        write=os.environ.get('LEADERSHIP_WRITE_DSN')
        if not write: parser.error('LEADERSHIP_WRITE_DSN required')
        with psycopg.connect(write,autocommit=True) as conn:
            # Reject owner/superuser writers: only Leadership INSERT/SELECT grants.
            unsafe=conn.execute("""SELECT r.rolsuper OR EXISTS(
                SELECT 1 FROM pg_tables t WHERE t.schemaname='public'
                AND t.tablename NOT IN ('flow_v3_leadership_run','flow_v3_leadership_snapshot')
                AND (has_table_privilege(current_user,quote_ident(t.schemaname)||'.'||quote_ident(t.tablename),'INSERT,UPDATE,DELETE,TRUNCATE')))
                FROM pg_roles r WHERE r.rolname=current_user""").fetchone()[0]
            if unsafe: parser.error('Leadership writer has forbidden source privileges')
            result.update(publish(conn,rows,audit,start))
    print(json.dumps(result,default=str,ensure_ascii=False))


if __name__=='__main__': main()
