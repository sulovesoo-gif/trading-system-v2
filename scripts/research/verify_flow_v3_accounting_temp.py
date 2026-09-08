"""Production implementation E2E using session-local TEMP copies only.

Requires the additive migration to exist. No permanent rows are written and
no PostgreSQL sequence is advanced: source rows retain their original IDs.
"""
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import psycopg
from dotenv import load_dotenv
from src.repository.database import DatabaseSettings
from src.flow_v3.accounting_repository import PaperAccountingRepository


class TempPool:
    def __init__(self,conn): self.conn=conn
    @contextmanager
    def connection(self): yield self.conn


def main():
    load_dotenv(ROOT/'.env')
    conn=psycopg.connect(**DatabaseSettings.from_environment().connection_kwargs(),autocommit=True)
    try:
        tables=['flow_v3_strategy_master','flow_v3_paper_trade','raw_stock_daily',
                'flow_v3_paper_accounting_queue','flow_v3_paper_accounting_lot',
                'flow_v3_paper_accounting_capital','flow_v3_paper_accounting_daily']
        for table in tables:
            conn.execute(f'CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL)')
        conn.execute('SET search_path=pg_temp,public')
        ids=['FV3008243','FV3008241','FV3005688','FV3000001']
        conn.execute('INSERT INTO pg_temp.flow_v3_strategy_master SELECT * FROM public.flow_v3_strategy_master WHERE strategy_id=ANY(%s)',(ids,))
        conn.execute('INSERT INTO pg_temp.flow_v3_paper_trade SELECT * FROM public.flow_v3_paper_trade WHERE strategy_id=ANY(%s)',(ids,))
        conn.execute("""INSERT INTO pg_temp.raw_stock_daily SELECT * FROM public.raw_stock_daily
          WHERE trading_venue='KRX' AND stock_code IN ('0193T0','0197X0','0193W0','0193L0')""")
        repo=PaperAccountingRepository(TempPool(conn))
        for sid in ids:
            conn.execute('INSERT INTO pg_temp.flow_v3_paper_accounting_queue(strategy_id) VALUES(%s)',(sid,))
            repo.rebuild(sid)
        before=conn.execute('SELECT strategy_id,metrics FROM pg_temp.flow_v3_paper_accounting_capital ORDER BY 1').fetchall()
        assert len(before)==len(ids)
        conn.execute('INSERT INTO pg_temp.flow_v3_paper_accounting_queue(strategy_id) SELECT strategy_id FROM pg_temp.flow_v3_strategy_master')
        # New repository instance models process restart against the same durable state.
        restarted=PaperAccountingRepository(TempPool(conn))
        for sid in ids: restarted.rebuild(sid)
        after=conn.execute('SELECT strategy_id,metrics FROM pg_temp.flow_v3_paper_accounting_capital ORDER BY 1').fetchall()
        assert before==after,'restart changed capital'
        actual=conn.execute('SELECT count(*),count(DISTINCT paper_trade_id),max(quantity) FROM pg_temp.flow_v3_paper_accounting_lot').fetchone()
        assert actual[0]==actual[1] and actual[0]>0
        assert conn.execute('SELECT count(*) FROM pg_temp.flow_v3_paper_accounting_queue').fetchone()[0]==0
        diff=conn.execute("""SELECT count(*) FROM pg_temp.flow_v3_paper_trade t
          JOIN public.flow_v3_paper_trade original USING(paper_trade_id)
          WHERE original.net_return_pct IS NOT NULL
            AND abs(t.net_return_pct-original.net_return_pct)>0.00000002""").fetchone()[0]
        assert diff==0,'research cost divergence'
        regression=conn.execute("SELECT metrics FROM pg_temp.flow_v3_paper_accounting_lot WHERE paper_trade_id=230266").fetchone()
        assert regression is not None and regression[0]['exit_time'] is not None
        print({'paper_trade_id':230266,'result':regression[0]},flush=True)
        print({'temp_e2e':'PASS','strategies':len(ids),'lots':actual[0],
               'maximum_quantity':actual[2],'duplicate':0,'restart_capital_equal':True,
               'research_cost_mismatch':diff,'permanent_writes':0},flush=True)
        for sid,metrics in after: print(sid,metrics,flush=True)
    finally:
        conn.close()  # destroys every TEMP object/data on both success and failure
        print('TEMP session closed; permanent test fixture rows=0',flush=True)


if __name__=='__main__': main()
