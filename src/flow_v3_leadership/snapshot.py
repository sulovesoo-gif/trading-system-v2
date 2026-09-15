"""Atomic immutable daily aggregate publication, with separate writer credentials."""
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, time, timedelta

from psycopg.types.json import Jsonb

from . import source
from .research import POLICIES, PERIODS, VERSION, build_states, extended_trades, replay, unavailable, rank_rows

COLUMNS = tuple(p.lower()+'_'+w for p in POLICIES for w in PERIODS)


def jsonable(value):
    return json.loads(json.dumps(value,default=str,sort_keys=True))


def compute(conn, asof, research_start):
    if research_start>asof: raise ValueError('INVALID_RESEARCH_START')
    master=source.universe(conn); axes=source.capitals(conn); session=source.session(conn)
    start=datetime.combine(research_start,time(9)); end=datetime.combine(asof+timedelta(days=1),time())
    stock_codes=sorted({s.stock_code for s in master})
    price={s:source.prices(conn,s,start,end) for s in stock_codes}
    raw={s:source.bases(conn,s,start,end) for s in stock_codes}
    states={s:build_states(raw[s]) for s in stock_codes}
    days=conn.execute("""SELECT DISTINCT bar_time::date FROM raw_stock_minute
        WHERE stock_code IN ('000660','005930') AND data_source='KIS' AND trading_venue='KRX'
        AND collect_cycle='1MIN' AND bar_time>=%s AND bar_time<%s ORDER BY 1""",(start,end)).fetchall()
    expected={r[0] for r in days}
    if asof not in expected: raise ValueError('SNAPSHOT_DATE_HAS_NO_SOURCE_TRADING_DAY')
    # A late research_start would lose pre-existing HOLD responsibilities.
    first=conn.execute("""SELECT min(t.entry_signal_time)::date FROM flow_v3_paper_trade t
        JOIN flow_v3_strategy_master m USING(strategy_id) WHERE m.direction='LONG'
        AND m.is_enabled='Y' AND m.stock_code IN ('000660','005930')""").fetchone()[0]
    if first and research_start>first: raise ValueError('RESEARCH_START_LOSES_HOLD_HISTORY')
    audits={}
    for stock in stock_codes:
        rows=states[stock]; ext=[r for r in rows if session.extended_start<=r.bar_time.time()<session.extended_end]
        ext_price=[t for t in price[stock].times if session.extended_start<=t.time()<=session.extended_end]
        audits[stock]=dict(raw_minutes=len(rows),raw_start=min((r.bar_time for r in rows),default=None),
            raw_end=max((r.bar_time for r in rows),default=None),extended_minutes=len(ext),
            extended_price_bars=len(ext_price),raw_venue='KRX',execution_tr='H0STCNT0',
            price_source='KIS/KRX/1MIN/raw_stock_minute',issues=[])
        observed={r.business_date for r in ext}
        if observed!=expected: audits[stock]['issues'].append('EXTENDED_RAW_COVERAGE_INCOMPLETE')
        if any(not any(r.business_date==d and r.bar_time.time()==time(9) for r in rows) for d in expected):
            audits[stock]['issues'].append('REGULAR_WARMUP_RAW_INCOMPLETE')
        if {t.date() for t in ext_price}!=expected:
            audits[stock]['issues'].append('EXTENDED_PRICE_UNAVAILABLE')
    groups=iter(source.regular_groups(conn,start,end)); current=next(groups,None)
    output=[]
    for strategy in master:
        while current and current[0]<strategy.strategy_id: current=next(groups,None)
        trades=current[1] if current and current[0]==strategy.strategy_id else []
        regular,issues=source.regular_prices(trades,price[strategy.stock_code],asof,session)
        policy_trades={'REGULAR':(regular,issues)}
        for policy in POLICIES[1:]:
            problems=audits[strategy.stock_code]['issues']
            if problems:
                policy_trades[policy]=([],problems)
            else:
                policy_trades[policy]=extended_trades(strategy,states[strategy.stock_code],
                    price[strategy.stock_code],policy,session,asof)
        for axis in axes:
            row=dict(snapshot_date=asof,strategy_id=strategy.strategy_id,capital_base=axis['amount'],
                stock_code=strategy.stock_code,direction=strategy.direction,strategy_definition=asdict(strategy))
            for policy,(trades,issues) in policy_trades.items():
                metrics = ({w:unavailable('|'.join(issues)) for w in PERIODS} if issues
                           else replay(trades,axis['amount'],asof))
                for period in PERIODS: row[policy.lower()+'_'+period]=metrics[period]
            output.append(row)
    audit=dict(stocks=audits,session=asdict(session),capital_axes=axes,source_first_date=first,
        cost_contract='09A V0.8 STOCK_LONG 0.000140527 each-side notional',
        capital_contract='independent PAPER lots; no cash/slot/D+2 simulation',
        period_contract='continuous replay; realized capital at each period boundary; HOLD carried',
        raw_coverage_contract='extended metrics unavailable unless every source trading day has RAW warmup and extended price')
    return rank_rows(output),audit


def publish(conn, rows, audit, research_start):
    if not rows: raise ValueError('EMPTY_SNAPSHOT')
    rows=sorted(rows,key=lambda r:(r['strategy_id'],r['capital_base']))
    dates={r['snapshot_date'] for r in rows}
    if len(dates)!=1: raise ValueError('MULTIPLE_SNAPSHOT_DATES')
    asof=rows[0]['snapshot_date']
    ids={r['strategy_id'] for r in rows}; capitals={r['capital_base'] for r in rows}
    if len(rows)!=len(ids)*len(capitals): raise ValueError('INCOMPLETE_SNAPSHOT_UNIVERSE')
    if len({(r['strategy_id'],r['capital_base']) for r in rows})!=len(rows): raise ValueError('DUPLICATE_SNAPSHOT')
    payload=jsonable(dict(version=VERSION,research_start=research_start,rows=rows,audit=audit))
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with conn.transaction():
        conn.execute("SET LOCAL lock_timeout='5s'")
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('FLOW_LEADERSHIP:'+str(asof),))
        old=conn.execute('SELECT result_hash,row_count FROM flow_v3_leadership_run WHERE snapshot_date=%s',(asof,)).fetchone()
        if old:
            count=conn.execute('SELECT count(*) FROM flow_v3_leadership_snapshot WHERE snapshot_date=%s',(asof,)).fetchone()[0]
            if old!=(digest,len(rows)) or count!=len(rows):
                raise ValueError('IMMUTABLE_SNAPSHOT_CONFLICT')
            return dict(status='UNCHANGED',rows=len(rows),hash=digest)
        conn.execute("""INSERT INTO flow_v3_leadership_run
            (snapshot_date,research_start,version,universe_count,capital_count,row_count,result_hash,source_audit)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
            (asof,research_start,VERSION,len(ids),len(capitals),len(rows),digest,Jsonb(payload['audit'])))
        fields=('snapshot_date','strategy_id','capital_base','stock_code','direction','strategy_definition')+COLUMNS
        sql='INSERT INTO flow_v3_leadership_snapshot ('+','.join(fields)+') VALUES ('+','.join(['%s']*len(fields))+')'
        with conn.cursor() as cur:
            cur.executemany(sql,[tuple(Jsonb(jsonable(r[f])) if f=='strategy_definition' or f in COLUMNS else r[f]
                                      for f in fields) for r in rows])
    return dict(status='INSERTED',rows=len(rows),hash=digest)
