"""Audit first; --apply persists additive overlays and requeues whole strategies.

No original PAPER or RAW row is updated or deleted. No broker imports.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from psycopg.types.json import Jsonb
from src.repository.database import DatabaseSettings, create_connection_pool
from src.flow_v3.contract_correction import CONTRACT, VERSION

SQL = """WITH candidates AS MATERIALIZED (
 SELECT t.*,m.execution_code,
        t.entry_signal_time::time>TIME '15:18' AS excluded
 FROM flow_v3_paper_trade t JOIN flow_v3_strategy_master m USING(strategy_id)
 WHERE m.exit_policy_code='SIGNAL_EOD' AND t.trade_status<>'CANCELLED'
 AND (t.entry_signal_time::time>TIME '15:18' OR t.exit_reason='FORCED_EOD'
      OR t.normal_exit_signal_time::time>TIME '15:18'
      OR (t.trade_status='OPEN' AND t.trade_date<CURRENT_DATE))
), price_keys AS (
 SELECT DISTINCT execution_code,trade_date FROM candidates WHERE NOT excluded
), prices AS MATERIALIZED (
 SELECT k.*,r.bar_time,r.open_price FROM price_keys k LEFT JOIN LATERAL (
  SELECT bar_time,open_price FROM raw_stock_minute
  WHERE stock_code=k.execution_code AND trading_venue='KRX' AND collect_cycle='1MIN'
   AND bar_time>=k.trade_date::timestamp AND bar_time<=k.trade_date+TIME '15:19'
   AND open_price>0 ORDER BY bar_time DESC,collected_at DESC LIMIT 1
 ) r ON true
)
SELECT t.paper_trade_id,t.strategy_id,t.trade_date,t.entry_signal_time,t.entry_execution_time,
 t.actual_exit_time,t.actual_exit_price,to_jsonb(t)-'execution_code'-'excluded',
 CASE WHEN NOT t.excluded THEN p.bar_time END,
 CASE WHEN NOT t.excluded THEN p.open_price END,t.excluded
FROM candidates t LEFT JOIN prices p USING(execution_code,trade_date)
ORDER BY t.paper_trade_id"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    pool = create_connection_pool(DatabaseSettings.from_environment())
    try:
        with pool.connection() as c, c.transaction():
            # Same lock ordering as worker; block no orders or unrelated services.
            c.execute("SET LOCAL lock_timeout='30s'")
            c.execute("SET LOCAL statement_timeout='120s'")
            if args.apply:
                c.execute("SELECT pg_advisory_xact_lock(hashtext('FLOW_V3_PAPER_ACCOUNTING'))")
                c.execute("SELECT pg_advisory_xact_lock(hashtext('FLOW_V3_PAPER_RUNTIME'))")
            else:
                c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            rows = c.execute(SQL).fetchall()
            excluded = [r for r in rows if r[10]]
            invalid = [r for r in rows if not r[10] and (r[8] is None or r[8]<r[4])]
            print(json.dumps(dict(candidates=len(rows),excluded=len(excluded),
                excluded_strategies=len({r[1] for r in excluded}),
                excluded_days=len({r[2] for r in excluded}),
                missing_or_reversed=len(invalid),first_invalid=invalid[:1]),default=str),flush=True)
            if invalid:
                raise RuntimeError('CORRECTED_PROXY_MISSING_OR_BEFORE_ENTRY')
            if not args.apply:
                return
            for r in rows:
                tid,sid,day,signal,entry,oldtime,oldprice,original,newtime,newprice,excluded = r
                prior = c.execute('SELECT original_row FROM flow_v3_paper_contract_correction '
                    'WHERE paper_trade_id=%s AND audit_version=%s',(tid,VERSION)).fetchone()
                if prior:
                    if prior[0] != original:
                        raise RuntimeError(f'SOURCE_CHANGED_AFTER_CORRECTION:{tid}')
                    continue
                c.execute("""INSERT INTO flow_v3_paper_correction_baseline
                    (audit_version,strategy_id,capital_before,daily_before)
                    SELECT %s,strategy_id,metrics,COALESCE((SELECT jsonb_object_agg(business_date,metrics)
                     FROM flow_v3_paper_accounting_daily d WHERE d.strategy_id=a.strategy_id),'{}'::jsonb)
                    FROM flow_v3_paper_accounting_capital a WHERE strategy_id=%s
                    ON CONFLICT DO NOTHING""",(VERSION,sid))
                c.execute("""INSERT INTO flow_v3_paper_contract_correction
                    (paper_trade_id,audit_version,strategy_id,trade_date,entry_signal_time,
                     entry_execution_time,original_exit_time,original_exit_price,original_row,
                     corrected_exit_time,corrected_exit_price,exclusion_reason,corrected_contract,
                     excluded_from_corrected_performance,excluded_from_corrected_compound)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tid,VERSION,sid,day,signal,entry,oldtime,oldprice,Jsonb(original),newtime,newprice,
                     'ENTRY_AFTER_EOD_CUTOFF' if excluded else None,CONTRACT,excluded,excluded))
            strategies = sorted({r[1] for r in rows})
            for sid in strategies:
                c.execute("""INSERT INTO flow_v3_paper_accounting_queue(strategy_id) VALUES(%s)
                    ON CONFLICT(strategy_id) DO UPDATE SET
                    generation=flow_v3_paper_accounting_queue.generation+1,
                    available_at=now(),last_error=NULL,updated_at=now()""",(sid,))
            print('CORRECTION_PERSISTED_REQUEUED',len(strategies),flush=True)
    finally:
        pool.close()


if __name__ == '__main__':
    main()
