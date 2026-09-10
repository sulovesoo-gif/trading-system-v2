"""Bounded read-only FLOW dashboard using Minute dashboard filter conventions."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from ..flow_v3.engine import PAIR_CODE
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
             'return':"(c.metrics->>'compound_return_pct')::numeric DESC NULLS LAST,m.strategy_id",
             'per_trade':"((c.metrics->>'net_pnl')::numeric / NULLIF((c.metrics->>'closed_count')::numeric,0)) DESC NULLS LAST,m.strategy_id"}
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


def _strategy(cur, strategy_id):
    cur.execute('''SELECT strategy_id,stock_code,direction,execution_code,entry_family_code,
        entry_fast_period,entry_slow_period,program_condition_code,exit_fast_period,
        exit_slow_period,exit_policy_code FROM flow_v3_strategy_master WHERE strategy_id=%s''', (strategy_id,))
    rows = _dicts(cur)
    if not rows:
        raise KeyError('전략을 찾을 수 없습니다.')
    return rows[0]


def strategy_detail_payload(pool, query):
    get = lambda key, default='': (query.get(key) or [default])[0]
    end = date.fromisoformat(get('end', datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat()))
    start = date.fromisoformat(get('start', (end-timedelta(days=6)).isoformat()))
    if not 0 <= (end-start).days <= 365:
        raise ValueError('조회기간은 1~366일로 지정해 주세요.')
    page = max(1, min(1000, int(get('page', '1'))))
    with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SET LOCAL statement_timeout='5s'")
        strategy = _strategy(cur, get('strategy_id'))
        cur.execute('''SELECT t.paper_trade_id,t.trade_status,t.entry_signal_time,t.entry_execution_time,
            t.entry_execution_price,t.normal_exit_signal_time,t.actual_exit_time,t.actual_exit_price,t.exit_reason,
            a.metrics->>'net_pnl' AS accounting_net_pnl,a.metrics->>'projection_current' AS accounting_current
            FROM flow_v3_paper_trade t LEFT JOIN flow_v3_paper_accounting_lot a USING(paper_trade_id)
            WHERE t.strategy_id=%s AND t.entry_signal_time>=%s::date AND t.entry_signal_time<%s::date
            ORDER BY t.entry_signal_time DESC,t.paper_trade_id DESC LIMIT 51 OFFSET %s''',
            (strategy['strategy_id'], start, end+timedelta(days=1), (page-1)*50))
        rows = _dicts(cur)
    return dict(strategy=strategy, items=rows[:50], has_more=len(rows)>50, page=page,
                start=start, end=end, range_basis='ENTRY_SIGNAL_TIME')


def _signal_window(cur, strategy, signal_time, kind):
    if signal_time is None:
        return []
    fast, slow = strategy[kind+'_fast_period'], strategy[kind+'_slow_period']
    pair = PAIR_CODE.get((fast, slow))
    cur.execute('''SELECT bar_time,underlying_close,aggressive_buy_amount,aggressive_sell_amount,
        flow_value,velocity_value,program_net_flow,program_velocity,long_absorption,short_absorption,
        quality_code,execution_source_gap,program_source_gap,orderbook_source_gap,
        flow_averages->>%s AS flow_fast,flow_averages->>%s AS flow_slow,
        velocity_averages->>%s AS velocity_fast,velocity_averages->>%s AS velocity_slow,
        flow_crosses->>%s AS flow_cross,velocity_crosses->>%s AS velocity_cross,
        bar_time=%s AS is_signal
        FROM flow_v3_minute_state WHERE stock_code=%s AND bar_time BETWEEN %s AND %s
        ORDER BY bar_time LIMIT 11''',
        (str(fast),str(slow),str(fast),str(slow),pair,pair,signal_time,strategy['stock_code'],
         signal_time-timedelta(minutes=5),signal_time+timedelta(minutes=5)))
    return _dicts(cur)


def trade_detail_payload(pool, query):
    get = lambda key: (query.get(key) or [''])[0]
    trade_id = int(get('paper_trade_id'))
    if trade_id <= 0:
        raise ValueError('유효한 거래 ID가 필요합니다.')
    with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SET LOCAL statement_timeout='5s'")
        strategy = _strategy(cur, get('strategy_id'))
        cur.execute('''SELECT t.paper_trade_id,t.entry_signal_time,t.entry_flow_value,t.entry_velocity_value,
            t.entry_program_value,t.entry_quality_code,t.normal_exit_signal_time,t.normal_exit_flow_value,
            t.normal_exit_velocity_value,t.exit_reason,t.actual_exit_time,t.actual_exit_price,
            e.event_context
            FROM flow_v3_paper_trade t LEFT JOIN flow_v3_runtime_entry_event e
              ON e.strategy_id=t.strategy_id AND e.entry_event_key=t.entry_event_key
            WHERE t.paper_trade_id=%s AND t.strategy_id=%s''', (trade_id,strategy['strategy_id']))
        rows = _dicts(cur)
        if not rows:
            raise KeyError('해당 전략의 거래를 찾을 수 없습니다.')
        trade = rows[0]
        entry = _signal_window(cur,strategy,trade['entry_signal_time'],'entry')
        exit_rows = _signal_window(cur,strategy,trade['normal_exit_signal_time'],'exit')
    return dict(strategy=strategy, trade=trade, entry_window=entry, exit_window=exit_rows,
                exit_source='VELOCITY' if strategy['entry_family_code']=='F2' else 'FLOW',
                exit_direction='하향교차' if strategy['direction']=='LONG' else '상향교차')
