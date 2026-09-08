"""Transactional PAPER projection worker, isolated from RAW and trading services."""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from psycopg.types.json import Jsonb

from .accounting import AccountingError, VERSION, calculate


def json_value(value):
    return Jsonb(json.loads(json.dumps(value, default=str)))


class PaperAccountingRepository:
    def __init__(self, pool):
        self.pool = pool

    def run_batch(self, limit=50):
        result = dict(processed=0, blocked=0, lock_acquired=False)
        with self.pool.connection() as guard:
            acquired = guard.execute("SELECT pg_try_advisory_lock(hashtext('FLOW_V3_PAPER_ACCOUNTING'))").fetchone()[0]
            guard.commit()
            if not acquired:
                return result
            result['lock_acquired'] = True
            try:
                with self.pool.connection() as conn:
                    ids = conn.execute("""SELECT strategy_id FROM flow_v3_paper_accounting_queue
                        WHERE available_at<=now() ORDER BY updated_at,strategy_id LIMIT %s""", (limit,)).fetchall()
                for (strategy_id,) in ids:
                    try:
                        self.rebuild(strategy_id)
                        result['processed'] += 1
                    except Exception as exc:
                        # Transaction already rolled back. Keep other strategies running;
                        # SQLSTATE/type only, never SQL parameters or account secrets.
                        reason = str(exc) if isinstance(exc, AccountingError) else type(exc).__name__
                        with self.pool.connection() as conn:
                            conn.execute("""UPDATE flow_v3_paper_accounting_queue SET last_error=%s,
                                available_at=now()+interval '60 seconds' WHERE strategy_id=%s""",
                                         (reason, strategy_id))
                        result['blocked'] += 1
            finally:
                guard.execute("SELECT pg_advisory_unlock(hashtext('FLOW_V3_PAPER_ACCOUNTING'))")
                guard.commit()
        return result

    def rebuild(self, strategy_id):
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            # A concurrent trade update cannot be lost between snapshot and queue removal.
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            cur.execute("SET LOCAL statement_timeout='30s'")
            cur.execute("SET LOCAL lock_timeout='3s'")
            cur.execute('SELECT generation FROM flow_v3_paper_accounting_queue WHERE strategy_id=%s', (strategy_id,))
            q = cur.fetchone()
            if not q:
                return
            generation = q[0]
            cur.execute('SELECT execution_code FROM flow_v3_strategy_master WHERE strategy_id=%s', (strategy_id,))
            execution_code = cur.fetchone()[0]
            cur.execute("""SELECT paper_trade_id,trade_status,entry_execution_time,entry_execution_price,
                 actual_exit_time,actual_exit_price,entry_signal_time,normal_exit_signal_time
                 FROM flow_v3_paper_trade
                 WHERE strategy_id=%s ORDER BY entry_execution_time,paper_trade_id""", (strategy_id,))
            keys = [x.name for x in cur.description]
            trades = [dict(zip(keys, row)) for row in cur.fetchall()]
            active = [t for t in trades if t['trade_status'] != 'CANCELLED']
            price_date = price = None
            if active:
                first = active[0]['entry_execution_time']
                if first is None:
                    raise AccountingError('MISSING_ENTRY_TIME')
                # Global last complete KRX trading date prevents a stale product quote
                # from silently standing in for a missing price on the required day.
                cur.execute("""SELECT trade_date,close_price FROM raw_stock_daily
                    WHERE stock_code=%s AND trading_venue='KRX' AND collect_cycle='DAILY'
                      AND data_source='KIS' AND close_price>0 AND trade_date=(
                        SELECT max(trade_date) FROM raw_stock_daily
                        WHERE trading_venue='KRX' AND collect_cycle='DAILY'
                          AND data_source='KIS' AND trade_date<%s)
                    ORDER BY collected_at DESC LIMIT 1""", (execution_code, first.date()))
                previous = cur.fetchone()
                if previous is None:
                    raise AccountingError('MISSING_PREVIOUS_KRX_CLOSE')
                price_date, price = previous
                lots, daily, summary = calculate(trades, previous_close=price)
            else:
                lots, daily = {}, {}
                summary = dict(trade_count=0, closed_count=0, open_count=0,
                               initial_capital=None, current_capital=None,
                               reason='NO_ENTRY_YET')
            # Invalidate obsolete projections without removing evidence/source records.
            cur.execute("""UPDATE flow_v3_paper_accounting_lot
                SET metrics=metrics || '{"projection_current":false}'::jsonb
                WHERE strategy_id=%s""", (strategy_id,))
            cur.execute("""UPDATE flow_v3_paper_accounting_daily
                SET metrics=metrics || '{"projection_current":false}'::jsonb
                WHERE strategy_id=%s""", (strategy_id,))
            for lot in lots.values():
                lot['projection_current'] = True
            if lots:
                cur.executemany("""INSERT INTO flow_v3_paper_accounting_lot
                    (paper_trade_id,strategy_id,quantity,calculation_version,metrics)
                    VALUES(%s,%s,%s,%s,%s) ON CONFLICT(paper_trade_id) DO UPDATE SET
                    quantity=EXCLUDED.quantity,calculation_version=EXCLUDED.calculation_version,
                    metrics=EXCLUDED.metrics,calculated_at=now()""",
                            [(tid,strategy_id,lot['quantity'],VERSION,json_value(lot))
                             for tid,lot in lots.items()])
                # Same formula as historical research; no source price or lifecycle writes.
                cur.executemany("""UPDATE flow_v3_paper_trade SET gross_return_pct=%s,net_return_pct=%s
                    WHERE paper_trade_id=%s AND
                     (gross_return_pct IS DISTINCT FROM %s::numeric(20,8)
                      OR net_return_pct IS DISTINCT FROM %s::numeric(20,8))""",
                            [(lot['gross_return_pct'],lot['net_return_pct'],tid,
                              lot['gross_return_pct'],lot['net_return_pct']) for tid,lot in lots.items()])
            for day, metrics in daily.items():
                metrics['projection_current'] = True
                cur.execute("""INSERT INTO flow_v3_paper_accounting_daily(strategy_id,business_date,metrics)
                    VALUES(%s,%s,%s) ON CONFLICT(strategy_id,business_date) DO UPDATE SET
                    metrics=EXCLUDED.metrics,calculated_at=now()""", (strategy_id,day,json_value(metrics)))
            cur.execute("""INSERT INTO flow_v3_paper_accounting_capital
                (strategy_id,execution_code,initial_price_date,initial_price,source_generation,calculation_version,metrics)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(strategy_id) DO UPDATE SET
                execution_code=EXCLUDED.execution_code,initial_price_date=EXCLUDED.initial_price_date,
                initial_price=EXCLUDED.initial_price,source_generation=EXCLUDED.source_generation,
                calculation_version=EXCLUDED.calculation_version,metrics=EXCLUDED.metrics,calculated_at=now()""",
                        (strategy_id,execution_code,price_date,price,generation,VERSION,json_value(summary)))
            cur.execute('DELETE FROM flow_v3_paper_accounting_queue WHERE strategy_id=%s AND generation=%s',
                        (strategy_id,generation))
