"""One consolidated audit after bootstrap; optional bounded derived-only requeue.

Never writes source trade fields, RAW, live requests, operations or authorization.
"""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings,create_connection_pool
from src.flow_v3.accounting_repository import PaperAccountingRepository


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--recheck-three',action='store_true')
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    pool=create_connection_pool(DatabaseSettings.from_environment())
    try:
        with pool.connection() as guard:
            guard.execute("SELECT pg_advisory_lock(hashtext('FLOW_V3_PAPER_ACCOUNTING'))")
            guard.commit()
            try:
                with pool.connection() as c:
                    pending=c.execute('SELECT count(*),count(*) FILTER(WHERE last_error IS NOT NULL) FROM flow_v3_paper_accounting_queue').fetchone()
                    print('queue',pending,flush=True)
                    if pending[0]: raise RuntimeError('BOOTSTRAP_NOT_COMPLETE')
                    facts=c.execute("""SELECT
                      count(*) AS source_trades,
                      count(*) FILTER(WHERE t.trade_status='CLOSED' AND t.actual_exit_time=t.entry_execution_time) AS equal_time,
                      count(*) FILTER(WHERE NOT coalesce(x.excluded_from_corrected_performance,false)
                         AND t.trade_status='CLOSED' AND coalesce(x.corrected_exit_time,t.actual_exit_time)<t.entry_execution_time) AS reversed_time,
                      count(*) FILTER(WHERE t.trade_status<>'CANCELLED'
                         AND NOT coalesce(x.excluded_from_corrected_performance,false)
                         AND (l.paper_trade_id IS NULL OR l.metrics->>'projection_current'<>'true')) AS missing_projections,
                      count(*) FILTER(WHERE t.trade_status='CLOSED'
                         AND NOT coalesce(x.excluded_from_corrected_performance,false) AND
                         ((l.metrics->>'net_return_pct') IS NULL OR abs((l.metrics->>'net_return_pct')::numeric
                         -(100*(coalesce(x.corrected_exit_price,t.actual_exit_price)/t.entry_execution_price-1)-0.0693054))>0.00000002)) AS cost_mismatch,
                      max(l.quantity) AS max_quantity
                    FROM flow_v3_paper_trade t LEFT JOIN flow_v3_paper_accounting_lot l USING(paper_trade_id)
                    LEFT JOIN flow_v3_paper_contract_correction x ON x.paper_trade_id=t.paper_trade_id
                      AND x.audit_version='FLOW_V3_EOD_1519_V1'""").fetchone()
                    print('source_equal_reversed_missing_cost_maxqty',facts,flush=True)
                    assert facts[2]==facts[3]==facts[4]==0
                    stats=c.execute("""SELECT count(*),count(DISTINCT strategy_id),
                      count(*) FILTER(WHERE metrics->>'initial_capital' IS NOT NULL),
                      count(*) FILTER(WHERE abs((metrics->>'current_capital')::numeric
                        -(metrics->>'initial_capital')::numeric-(metrics->>'net_pnl')::numeric)>0.000001),
                      count(*) FILTER(WHERE initial_price IS NOT NULL AND
                        abs((metrics->>'initial_capital')::numeric-initial_price*1.5)>0.000001)
                      FROM flow_v3_paper_accounting_capital""").fetchone()
                    print('capital_total_distinct_initialized_invariant_initial',stats,flush=True)
                    assert stats[0]==stats[1]==9600 and stats[3]==stats[4]==0
                    other=c.execute("""SELECT
                      (SELECT count(*)-count(DISTINCT paper_trade_id) FROM flow_v3_paper_accounting_lot),
                      (SELECT count(*) FROM flow_v3_paper_accounting_lot l LEFT JOIN flow_v3_paper_trade t USING(paper_trade_id) WHERE t.paper_trade_id IS NULL),
                      (SELECT count(*) FROM flow_v3_paper_accounting_daily WHERE metrics->>'projection_current'='true')""").fetchone()
                    print('duplicate_orphan_daily',other,flush=True)
                    assert other[0]==other[1]==0
                    sample=c.execute("SELECT metrics FROM flow_v3_paper_accounting_lot WHERE paper_trade_id=230266").fetchone()
                    print('230266',sample[0],flush=True)
                if args.recheck_three:
                    ids=['FV3000001','FV3008243','FV3005688']
                    with pool.connection() as c:
                        before=c.execute('SELECT strategy_id,metrics FROM flow_v3_paper_accounting_capital WHERE strategy_id=ANY(%s) ORDER BY 1',(ids,)).fetchall()
                        for sid in ids:
                            c.execute('INSERT INTO flow_v3_paper_accounting_queue(strategy_id) VALUES(%s) ON CONFLICT DO NOTHING',(sid,))
                    repo=PaperAccountingRepository(pool)
                    for sid in ids: repo.rebuild(sid)
                    with pool.connection() as c:
                        after=c.execute('SELECT strategy_id,metrics FROM flow_v3_paper_accounting_capital WHERE strategy_id=ANY(%s) ORDER BY 1',(ids,)).fetchall()
                    assert before==after,'REBUILD_CHANGED_CAPITAL'
                    print('THREE_STRATEGY_PRODUCTION_REBUILD_IDENTICAL',flush=True)
                print('PRODUCTION_ACCOUNTING_AUDIT_PASS',flush=True)
            finally:
                guard.execute("SELECT pg_advisory_unlock(hashtext('FLOW_V3_PAPER_ACCOUNTING'))")
                guard.commit()
    finally: pool.close()


if __name__=='__main__': main()
