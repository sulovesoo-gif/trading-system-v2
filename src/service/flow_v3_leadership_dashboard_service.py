"""New SELECT-only Leadership API. Does not import the operating Dashboard."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from src.flow_v3_leadership.source import capitals
from src.flow_v3_leadership.research import POLICIES, PERIODS


def dictionaries(cur):
    return [dict(zip([c.name for c in cur.description],r)) for r in cur.fetchall()]


def parameters(query):
    get=lambda k,d=None:(query.get(k) or [d])[0]
    policy=get('policy','REGULAR'); period=get('period','daily')
    if policy not in POLICIES or period not in PERIODS: raise ValueError('INVALID_AXIS')
    top=int(get('top','50'))
    if top not in (50,100): raise ValueError('INVALID_TOP')
    try:
        capital=Decimal(get('capital'))  # UI supplies the active common-code default.
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('INVALID_CAPITAL') from exc
    if not capital.is_finite() or capital<=0: raise ValueError('INVALID_CAPITAL')
    return get,policy,period,top,capital


def options(conn):
    axes=capitals(conn)
    dates=conn.execute('SELECT snapshot_date FROM flow_v3_leadership_run ORDER BY snapshot_date DESC LIMIT 370').fetchall()
    return dict(capitals=axes,dates=[r[0] for r in dates],policies=POLICIES,periods=PERIODS)


def ranking(conn,query):
    get,policy,period,top,capital=parameters(query)
    asof=date.fromisoformat(get('date'))
    col=policy.lower()+'_'+period  # Enumerated identifiers only; never raw query input.
    where=['s.snapshot_date=%s','s.capital_base=%s']; args=[asof,capital]
    for name,allowed in [('stock_code',('000660','005930')),('direction',('LONG','SHORT'))]:
        value=get(name,'')
        if value:
            if value not in allowed: raise ValueError('INVALID_FILTER')
            where.append('s.'+name+'=%s');args.append(value)
    exit_policy=get('exit_policy','')
    if exit_policy:
        if exit_policy not in ('SIGNAL_EOD','SIGNAL_HOLD'): raise ValueError('INVALID_EXIT_POLICY')
        where.append("s.strategy_definition->>'exit_policy_code'=%s");args.append(exit_policy)
    search=get('search','').strip()
    if len(search)>40: raise ValueError('SEARCH_TOO_LONG')
    if search: where.append('s.strategy_id ILIKE %s');args.append('%'+search+'%')
    sort=get('sort','compound_return')
    if sort not in ('compound_return','final_capital','net_profit'): raise ValueError('INVALID_SORT')
    metrics=f's.{col}'
    cur=conn.execute(f"""SELECT s.strategy_id,s.stock_code,s.direction,s.capital_base,s.strategy_definition,
        {metrics} AS metrics,p.{col}->>'rank' AS previous_rank,
        {','.join('s.'+p.lower()+'_'+period+' AS '+p.lower() for p in POLICIES)}
        FROM flow_v3_leadership_snapshot s LEFT JOIN flow_v3_leadership_snapshot p
          ON p.strategy_id=s.strategy_id AND p.capital_base=s.capital_base
          AND p.snapshot_date=(SELECT max(snapshot_date) FROM flow_v3_leadership_run WHERE snapshot_date<%s)
        WHERE {' AND '.join(where)}
        ORDER BY ({metrics}->>%s)::numeric DESC NULLS LAST,
          ({metrics}->>'net_profit')::numeric DESC NULLS LAST,s.strategy_id LIMIT %s""",[asof]+args+[sort,top])
    rows=dictionaries(cur)
    for r in rows:
        rank=r['metrics'].get('rank'); previous=r['previous_rank']
        r['rank_change']=int(previous)-int(rank) if previous and rank else None
        r['rank_label']='미산출' if rank is None else 'NEW' if previous is None else str(r['rank_change'])
    meta=conn.execute('SELECT row_count,universe_count,capital_count,source_audit FROM flow_v3_leadership_run WHERE snapshot_date=%s',(asof,)).fetchone()
    return dict(rows=rows,snapshot_date=asof,policy=policy,period=period,
        metadata=dict(zip(('row_count','universe_count','capital_count','source_audit'),meta)) if meta else None,
        rank_contract='전체 LONG 본주 Universe: 복리수익률 DESC, 순이익 DESC, 전략ID ASC')


def history(conn,query):
    get,policy,period,top,capital=parameters(query)
    asof=date.fromisoformat(get('date')); col=policy.lower()+'_'+period
    ids=list(dict.fromkeys(get('strategies','').split(',')))
    if not ids or len(ids)>100 or any(not s.startswith('FV3') or not s.isalnum() or len(s)>20 for s in ids):
        raise ValueError('INVALID_STRATEGIES')
    cur=conn.execute(f"""SELECT snapshot_date,strategy_id,{col} AS metrics,
        {','.join(p.lower()+'_'+period+' AS '+p.lower() for p in POLICIES)}
        FROM flow_v3_leadership_snapshot WHERE capital_base=%s AND snapshot_date BETWEEN %s AND %s
        AND strategy_id=ANY(%s) ORDER BY snapshot_date,strategy_id LIMIT 3200""",
        (capital,asof-timedelta(days=30),asof,ids))
    return dict(rows=dictionaries(cur))
