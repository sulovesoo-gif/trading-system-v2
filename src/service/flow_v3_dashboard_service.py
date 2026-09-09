"""Bounded read-only FLOW dashboard using Minute dashboard filter conventions."""
from datetime import date, datetime, timedelta
from .minute_ma_dashboard_service import _page, _page_size, _period_window, _dicts

LIVE_CANDIDATES = (
 'FV3008243','FV3008241','FV3009185','FV3009201','FV3009187','FV3009203',
 'FV3008227','FV3008225','FV3008211','FV3008209','FV3008084',
 'FV3005688','FV3005672','FV3004728','FV3004712')


def dashboard_payload(pool, query):
    get = lambda key, default: (query.get(key) or [default])[0]
    asof = date.fromisoformat(get('date', date.today().isoformat()))
    page, size = _page(get('page',1)), _page_size(get('page_size',20))
    period, start, end = _period_window(asof,get('period','ALL'))
    where, params = ['true'], []
    for col in ('direction','entry_family_code','program_condition_code','exit_policy_code'):
        value = get(col,'')
        if value:
            where.append(f'm.{col}=%s');params.append(value)
    if get('scope','CANDIDATES') == 'CANDIDATES':
        where.append('m.strategy_id=ANY(%s)');params.append(list(LIVE_CANDIDATES))
    if get('search',''):
        where.append('(m.strategy_id ILIKE %s OR m.stock_code ILIKE %s)')
        params.extend(['%'+get('search','')+'%']*2)
    if get('lifecycle','') == 'OPEN':
        where.append("COALESCE((c.metrics->>'open_count')::integer,0)>0")
    sorts = {'strategy':'m.strategy_id', 'capital':"(c.metrics->>'current_capital')::numeric DESC NULLS LAST,m.strategy_id",
             'return':"(c.metrics->>'compound_return_pct')::numeric DESC NULLS LAST,m.strategy_id"}
    order = sorts.get(get('sort','strategy'),sorts['strategy'])
    condition = ' AND '.join(where)
    joins = 'FROM flow_v3_strategy_master m LEFT JOIN flow_v3_paper_accounting_capital c USING(strategy_id)'
    with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SET LOCAL statement_timeout='5s'")
        cur.execute(f'SELECT count(*) {joins} WHERE {condition}',params)
        total=cur.fetchone()[0]
        cur.execute(f"""SELECT m.strategy_id,m.stock_code,m.direction,m.execution_code,
           m.entry_family_code,m.entry_fast_period,m.entry_slow_period,m.program_condition_code,
           m.exit_fast_period,m.exit_slow_period,m.exit_policy_code,m.is_enabled,
           c.metrics AS paper_cumulative,c.calculated_at,c.initial_price_date,c.initial_price,
           q.last_error,q.strategy_id IS NOT NULL AS accounting_pending
           {joins} LEFT JOIN flow_v3_paper_accounting_queue q USING(strategy_id)
           WHERE {condition} ORDER BY {order} LIMIT %s OFFSET %s""",params+[size,(page-1)*size])
        rows=_dicts(cur)
        cur.execute("SELECT to_regclass('public.flow_v3_live_preparation')")
        preparation_available=cur.fetchone()[0] is not None
        cur.execute("SELECT to_regclass('public.flow_v3_live_capital')")
        live_pipeline_available=cur.fetchone()[0] is not None
        for row in rows:
            row['live_capital']=None
            row['live_orders']=[]
            row['live_lots']=[]
            if live_pipeline_available:
                cur.execute('SELECT * FROM flow_v3_live_capital WHERE strategy_id=%s',(row['strategy_id'],))
                capitals=_dicts(cur)
                row['live_capital']=capitals[0] if capitals else None
                cur.execute("""SELECT i.status,count(*) AS count,COALESCE(sum(o.post_attempt_count),0) AS post_attempts
                    FROM flow_v3_live_intent i LEFT JOIN flow_v3_live_order o USING(intent_id)
                    WHERE i.strategy_id=%s GROUP BY i.status""",(row['strategy_id'],))
                row['live_orders']=_dicts(cur)
                cur.execute("""SELECT exposure_status,count(*) AS count,sum(bought_quantity-sold_quantity) AS open_quantity
                    FROM flow_v3_live_lot WHERE strategy_id=%s GROUP BY exposure_status""",(row['strategy_id'],))
                row['live_lots']=_dicts(cur)
            row['live_preparation']=None
            if preparation_available:
                cur.execute('SELECT * FROM flow_v3_live_preparation WHERE strategy_id=%s',(row['strategy_id'],))
                prep=_dicts(cur)
                row['live_preparation']=prep[0] if prep else None
            cur.execute("""SELECT sum((metrics->>'entry_count')::integer) AS entry_count,
                sum((metrics->>'closed_count')::integer) AS closed_count,
                sum((metrics->>'win_count')::integer) AS win_count,
                sum((metrics->>'loss_count')::integer) AS loss_count,
                sum((metrics->>'net_pnl')::numeric) AS net_pnl,
                (array_agg((metrics->>'starting_capital')::numeric ORDER BY business_date))[1] AS starting_capital,
                (array_agg((metrics->>'ending_capital')::numeric ORDER BY business_date DESC))[1] AS ending_capital,
                (array_agg((metrics->>'open_count')::integer ORDER BY business_date DESC))[1] AS open_count
                FROM flow_v3_paper_accounting_daily
                WHERE strategy_id=%s AND business_date<%s::date
                 AND (%s::timestamp IS NULL OR business_date>=%s::date)
                 AND metrics->>'projection_current'='true'""",
                        (row['strategy_id'],end,start,start))
            row['paper_period'] = _dicts(cur)[0]
            cur.execute("""SELECT trade_status,count(*) AS trade_count,
                COALESCE(sum(net_realized_pnl),0) AS net_realized_pnl
                FROM flow_v3_live_trade WHERE strategy_id=%s
                  AND entry_signal_time<%s GROUP BY trade_status""",(row['strategy_id'],end))
            row['live_actual'] = _dicts(cur)
            cur.execute("""SELECT operation_status,allocated_amount FROM flow_v3_strategy_operation
                WHERE strategy_id=%s AND effective_to IS NULL ORDER BY effective_from DESC,operation_id DESC LIMIT 1""",(row['strategy_id'],))
            op = _dicts(cur)
            row['operation'] = op[0] if op else None
        cur.execute("SELECT count(*),count(*) FILTER(WHERE is_enabled='Y') FROM flow_v3_strategy_master")
        master,enabled=cur.fetchone()
        cur.execute('SELECT count(*),count(*) FILTER(WHERE last_error IS NOT NULL) FROM flow_v3_paper_accounting_queue')
        pending,blocked=cur.fetchone()
    return dict(status='OK',items=rows,page=page,page_size=size,total_count=total,
                total_pages=max(1,(total+size-1)//size),period=period,period_from=start,period_to=end,
                strategy_master=master,paper_enabled=enabled,accounting_pending=pending,
                accounting_blocked=blocked,live_send_status='NOT_ACTIVATED_BY_THIS_RELEASE',
                shared_account_status='NOT_IMPLEMENTED', cumulative_scope='ALL_AVAILABLE_PAPER_HISTORY')
