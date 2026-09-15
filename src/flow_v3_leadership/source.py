"""Bounded SELECT-only source adapter. Caller owns a repeatable-read RO transaction."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from itertools import groupby

from src.flow_v3.models import MinuteBase, StrategyContract
from .research import Prices, Session

CAPITAL_GROUP = 'FLOW_LEADERSHIP_CAPITAL'


def capitals(conn):
    rows = conn.execute("""SELECT c.code,c.code_name,c.attr1,c.attr2,c.sort_order
        FROM common_code c JOIN common_code_group g USING(group_cd)
        WHERE c.group_cd=%s AND c.use_yn='Y' AND g.use_yn='Y'
        ORDER BY c.sort_order,c.code""", (CAPITAL_GROUP,)).fetchall()
    result=[]
    for code,name,amount,default,sort in rows:
        value=Decimal(amount)
        if not value.is_finite() or value<=0 or value!=value.to_integral_value():
            raise ValueError('INVALID_COMMON_CODE_CAPITAL:'+code)
        result.append(dict(code=code,name=name,amount=value,is_default=default=='Y',sort_order=sort))
    if not result or len({r['amount'] for r in result}) != len(result):
        raise ValueError('CAPITAL_CONFIG_MISSING_OR_DUPLICATE')
    if sum(r['is_default'] for r in result)>1:
        raise ValueError('CAPITAL_DEFAULT_NOT_UNIQUE')
    if not any(r['is_default'] for r in result):
        result[0]['is_default']=True  # Disabled default: first active DB sort order, no hardcoded amount.
    return result


def session(conn):
    row=conn.execute("""SELECT attr5 FROM common_code WHERE group_cd='MARKET'
        AND code='INTEGRATED' AND use_yn='Y'""").fetchone()
    if not row: raise ValueError('EXTENDED_MARKET_CONFIG_MISSING')
    end=time.fromisoformat(row[0])
    if end<=time(16): raise ValueError('INVALID_EXTENDED_SESSION_END')
    krx=conn.execute("""SELECT attr5 FROM common_code WHERE group_cd='MARKET'
        AND code='KRX' AND use_yn='Y'""").fetchone()
    if not krx: raise ValueError('REGULAR_MARKET_CONFIG_MISSING')
    return Session(extended_end=end,regular_market_end=time.fromisoformat(krx[0]))


def universe(conn):
    rows=conn.execute("""SELECT strategy_id,stock_code,direction,entry_family_code,
        entry_fast_period,entry_slow_period,program_condition_code,exit_fast_period,
        exit_slow_period,exit_policy_code,execution_code FROM flow_v3_strategy_master
        WHERE is_enabled='Y' AND direction='LONG' AND stock_code IN ('000660','005930')
        ORDER BY strategy_id""").fetchall()
    if not rows: raise ValueError('EMPTY_UNDERLYING_UNIVERSE')
    return [StrategyContract(*r) for r in rows]


def prices(conn, stock, start, end):
    # Group the complete source identity first. Conflicting KRX prices are not
    # resolved by arbitrary market-code selection or multiplication in a JOIN.
    rows=conn.execute("""SELECT bar_time,min(open_price),max(open_price)
        FROM raw_stock_minute WHERE stock_code=%s AND data_source='KIS'
        AND trading_venue='KRX' AND collect_cycle='1MIN' AND bar_time>=%s AND bar_time<%s
        GROUP BY bar_time ORDER BY bar_time""",(stock,start,end)).fetchall()
    if any(lo!=hi for _,lo,hi in rows): raise ValueError('AMBIGUOUS_PRICE_SOURCE:'+stock)
    return Prices([(at,lo) for at,lo,_ in rows])


def bases(conn, stock, start, end):
    """Three bulk minute queries per stock, not per strategy/capital/minute.

    Mirrors repository.minute_base's FLOW signs, program cumulative differences,
    date correction and book absorption. Original payloads are never modified.
    Stored KRX/H0STCNT0 provenance is retained even for observed post-16:00 RAW.
    """
    executions=conn.execute("""SELECT date_trunc('minute',source_event_time),
        sum(CASE WHEN execution_classification='1' THEN coalesce(current_price,0)::numeric*abs(coalesce(execution_volume,0)::numeric) ELSE 0 END),
        sum(CASE WHEN execution_classification='5' THEN coalesce(current_price,0)::numeric*abs(coalesce(execution_volume,0)::numeric) ELSE 0 END),
        (array_agg(current_price ORDER BY source_event_time DESC,receive_sequence DESC,event_index DESC)
            FILTER(WHERE current_price IS NOT NULL))[1],
        bool_or(source_gap_flag),count(*) FILTER(WHERE duplicate_flag)
        FROM raw_flow_execution WHERE stock_code=%s AND trading_venue='KRX' AND tr_id='H0STCNT0'
        AND source_event_time>=%s AND source_event_time<%s
        AND source_event_time::time>='09:00'
        GROUP BY 1 ORDER BY 1""",(stock,start,end)).fetchall()
    programs=conn.execute("""WITH corrected AS (
        SELECT *, CASE WHEN abs(extract(epoch FROM (source_event_time-received_at)))>=43200
          THEN received_at::date+source_event_time::time ELSE source_event_time END AS t
        FROM raw_flow_program WHERE stock_code=%s AND trading_venue='KRX'
        AND received_at>=%s::timestamp-interval '1 day' AND received_at<%s::timestamp+interval '1 day'
        ) SELECT DISTINCT ON(date_trunc('minute',t)) date_trunc('minute',t),
          coalesce(net_buy_execution_amount,0),source_gap_flag,duplicate_flag
        FROM corrected WHERE t>=%s AND t<%s
        ORDER BY date_trunc('minute',t),t DESC,receive_sequence DESC,event_index DESC""",
        (stock,start,end,start,end)).fetchall()
    books=conn.execute("""SELECT date_trunc('minute',source_event_time),
        (array_agg(total_bid_quantity ORDER BY source_event_time,received_at))[1],
        (array_agg(total_bid_quantity ORDER BY source_event_time DESC,received_at DESC))[1],
        (array_agg(total_ask_quantity ORDER BY source_event_time,received_at))[1],
        (array_agg(total_ask_quantity ORDER BY source_event_time DESC,received_at DESC))[1],
        count(*),bool_or(source_gap_flag),count(*) FILTER(WHERE duplicate_flag)
        FROM raw_flow_orderbook_5s WHERE stock_code=%s AND trading_venue='KRX'
        AND source_event_time>=%s AND source_event_time<%s GROUP BY 1 ORDER BY 1""",
        (stock,start,end)).fetchall()
    pg={r[0]:r[1:] for r in programs}; ob={r[0]:r[1:] for r in books}
    result=[]
    for at,buy,sell,close,gap,dups in executions:
        if close is None: continue
        cur,prev=pg.get(at),pg.get(at-timedelta(minutes=1))
        book=ob.get(at)
        result.append(MinuteBase(at.date(),stock,at,Decimal(close),buy,sell,buy-sell,
            cur[0]-prev[0] if cur and prev and at.date()==(at-timedelta(minutes=1)).date() else None,
            *(book[:4] if book else (None,)*4),snapshot_count=book[4] if book else 0,
            execution_source_gap=bool(gap),execution_duplicate_rows=dups,
            program_source_gap=not cur or bool(cur[1]),program_duplicate_rows=int(bool(cur and cur[2])),
            orderbook_source_gap=not book or bool(book[5]),orderbook_duplicate_rows=book[6] if book else 0))
    return result


def regular_groups(conn, start, end):
    """Server cursor: one strategy's trades in memory, authoritative correction overlay.

    PAPER prices are not reused for underlying execution. Only event/lifecycle
    times are read; STOCK prices are selected separately from KIS/KRX/1MIN.
    """
    with conn.cursor(name='leadership_paper_source') as cur:
        cur.itersize=4000
        cur.execute("""SELECT t.strategy_id,t.paper_trade_id,t.entry_signal_time,
          t.entry_execution_time,
          CASE WHEN x.paper_trade_id IS NOT NULL THEN x.corrected_exit_time ELSE t.actual_exit_time END AS actual_exit_time,
          CASE WHEN x.paper_trade_id IS NOT NULL THEN NULL ELSE t.normal_exit_signal_time END AS normal_exit_signal_time,
          CASE WHEN x.paper_trade_id IS NOT NULL THEN 'SIGNAL_EOD' ELSE t.exit_reason END AS exit_reason,
          m.exit_policy_code
          FROM flow_v3_paper_trade t JOIN flow_v3_strategy_master m USING(strategy_id)
          LEFT JOIN flow_v3_paper_contract_correction x ON x.paper_trade_id=t.paper_trade_id
            AND x.audit_version='FLOW_V3_EOD_1519_V1'
          WHERE m.is_enabled='Y' AND m.direction='LONG' AND m.stock_code IN ('000660','005930')
            AND t.trade_status IN ('OPEN','CLOSED') AND t.entry_signal_time>=%s AND t.entry_signal_time<%s
            AND t.entry_signal_time::time BETWEEN '09:00' AND '15:18'
            AND NOT coalesce(x.excluded_from_corrected_performance,false)
          ORDER BY t.strategy_id,t.entry_execution_time,t.paper_trade_id""",(start,end))
        keys=[d.name for d in cur.description]
        for sid,group in groupby(cur,key=lambda r:r[0]):
            yield sid,[dict(zip(keys,r)) for r in group]


def regular_prices(trades, price_source, asof, session):
    result=[]; issues=set()
    for original in trades:
        t=dict(original)
        at=t['entry_execution_time']
        if at is None or at not in price_source.values:
            issues.add('MISSING_ENTRY_PRICE');continue
        t['entry_execution_price']=price_source.values[at]
        end=t['actual_exit_time']
        if t['exit_policy_code']=='SIGNAL_EOD':
            # EOD's source and execution series are independent. Never forward-fill.
            normal=t['normal_exit_signal_time']
            if normal is None or normal.time()>session.regular_signal_end:
                price=price_source.eod(at.date(),session.regular_execution_end)
                if not price or price[0]<at:
                    issues.add('MISSING_EOD_PRICE');continue
                end,t['actual_exit_price']=price
                t['exit_reason']='SIGNAL_EOD'
                t['normal_exit_signal_time']=None
        if end and end.date()<=asof:
            if end not in price_source.values:
                issues.add('MISSING_EXIT_PRICE');continue
            t['actual_exit_price']=price_source.values[end]
        else:
            end=None;t['actual_exit_price']=None
        t['actual_exit_time']=end
        if end is not None and end<at:
            issues.add('INVALID_EXIT_TIME');continue
        result.append(t)
    return result, sorted(issues)
