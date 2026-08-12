"""Read-only VIDEO_STRATEGY dashboard queries, isolated from legacy payloads."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from uuid import UUID


def runs_payload(pool):
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("""
          SELECT r.run_id,r.created_at,r.start_date,r.end_date,r.status,r.parameters,
            (SELECT count(*) FROM research_signal_event e WHERE e.run_id=r.run_id),
            (SELECT count(*) FROM research_trade_cycle c WHERE c.run_id=r.run_id),
            (SELECT coalesce(sum(c.gross_realized_profit),0) FROM research_trade_cycle c WHERE c.run_id=r.run_id),
            (SELECT coalesce(sum(c.total_trading_cost),0) FROM research_trade_cycle c WHERE c.run_id=r.run_id),
            (SELECT coalesce(sum(c.realized_profit),0) FROM research_trade_cycle c WHERE c.run_id=r.run_id)
          FROM research_run r
          WHERE r.parameters->>'strategy_family'='VIDEO_STRATEGY'
          ORDER BY r.created_at DESC LIMIT 100""")
        names=("run_id","created_at","start_date","end_date","run_status","parameters",
               "event_count","cycle_count","gross_profit","trading_cost","net_profit")
        return {"status":"OK","runs":[dict(zip(names,row)) for row in cur.fetchall()]}


def replay_payload(pool,run_id):
    UUID(str(run_id))
    with pool.connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT parameters,start_date,end_date,status,created_at FROM research_run
          WHERE run_id=%s AND parameters->>'strategy_family'='VIDEO_STRATEGY'""",(run_id,))
        run=cur.fetchone()
        if run is None:return {"status":"NOT_FOUND"}
        cur.execute("""SELECT f.observation_time,f.price,f.ma20,f.ma10_direction,f.feature_detail,
            b.open_price,b.high_price,b.low_price,b.close_price,b.volume,f.data_status
          FROM research_feature f LEFT JOIN raw_stock_minute b
            ON b.stock_code=f.stock_code AND b.bar_time=f.observation_time
            AND b.data_source='KIS' AND b.market_code='KOSPI'
            AND b.trading_venue='INTEGRATED' AND b.collect_cycle='1MIN'
          WHERE f.run_id=%s AND f.stock_code='000660' ORDER BY f.observation_time""",(run_id,)); features=cur.fetchall()
        cur.execute("""SELECT e.event_id,e.signal_time,e.signal_type,e.direction,e.signal_price,e.event_detail,e.data_status
          FROM research_signal_event e WHERE e.run_id=%s ORDER BY e.signal_time,e.event_id""",(run_id,)); events=cur.fetchall()
        cur.execute("""SELECT p.event_id,p.execution_stock_code,p.execution_direction,p.trade_price,
            p.return_1m,p.return_3m,p.return_5m,p.return_10m,p.return_20m,p.return_30m,p.mfe,p.mae,p.data_status
          FROM research_video_event_performance p JOIN research_signal_event e ON e.event_id=p.event_id
          WHERE e.run_id=%s ORDER BY p.event_id,p.execution_stock_code""",(run_id,)); perf=cur.fetchall()
        cur.execute("""SELECT cycle_id,trade_stock_code,direction,entry_time,entry_price,exit_time,exit_price,
            gross_realized_profit,total_trading_cost,realized_profit,invested_return_rate,holding_seconds,
            exit_type,data_status
          FROM research_trade_cycle WHERE run_id=%s ORDER BY entry_time,trade_stock_code""",(run_id,)); cycles=cur.fetchall()
    cols=lambda names,rows:[dict(zip(names,row)) for row in rows]
    return {"status":"OK","run_id":run_id,"parameters":run[0],"start_date":run[1],"end_date":run[2],
      "run_status":run[3],"created_at":run[4],
      "features":cols(("time","price","sma20","sma20_direction","detail","open","high","low","close","volume","data_status"),features),
      "events":cols(("event_id","time","event_type","direction","price","detail","data_status"),events),
      "performance":cols(("event_id","execution_stock_code","execution_direction","trade_price","return_1m","return_3m","return_5m","return_10m","return_20m","return_30m","mfe","mae","data_status"),perf),
      "cycles":cols(("cycle_id","execution_stock_code","direction","entry_time","entry_price","exit_time","exit_price","gross_profit","trading_cost","net_profit","return_rate","holding_seconds","exit_type","data_status"),cycles)}


def performance_payload(pool, run_id, group_by="all", target="000660"):
    """Aggregate stored cycles only; this never recalculates strategy decisions."""
    UUID(str(run_id))
    if group_by not in {"trade","day","month","year","all"}:
        return {"status":"ERROR","message":"group_by 값이 올바르지 않습니다.","items":[]}
    if target not in {"000660","0193T0","0197X0"}:
        return {"status":"ERROR","message":"target 값이 올바르지 않습니다.","items":[]}
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT c.cycle_id,c.trading_date,c.trade_stock_code,c.direction,c.entry_time,c.entry_price,
            c.exit_time,c.exit_price,c.exit_type,c.holding_seconds,c.gross_realized_profit,c.total_trading_cost,
            c.realized_profit,c.invested_return_rate,
            (SELECT p.mfe FROM research_signal_event e JOIN research_video_event_performance p ON p.event_id=e.event_id
              WHERE e.run_id=c.run_id AND e.signal_time=c.entry_time AND e.signal_type IN ('LONG_ENTRY','SHORT_ENTRY')
                AND p.execution_stock_code=c.trade_stock_code ORDER BY e.event_id LIMIT 1),
            (SELECT p.mae FROM research_signal_event e JOIN research_video_event_performance p ON p.event_id=e.event_id
              WHERE e.run_id=c.run_id AND e.signal_time=c.entry_time AND e.signal_type IN ('LONG_ENTRY','SHORT_ENTRY')
                AND p.execution_stock_code=c.trade_stock_code ORDER BY e.event_id LIMIT 1)
          FROM research_trade_cycle c WHERE c.run_id=%s AND c.trade_stock_code=%s AND c.exit_time IS NOT NULL
          ORDER BY c.entry_time,c.cycle_id""",(run_id,target))
        names=("cycle_id","trading_date","execution_stock_code","direction","entry_time","entry_price","exit_time",
          "exit_price","exit_type","holding_seconds","gross_profit","trading_cost","net_profit","return_rate","mfe","mae")
        trades=[dict(zip(names,row)) for row in cur.fetchall()]
    groups=defaultdict(list)
    for row in trades:
        day=row["trading_date"]
        key=(str(row["cycle_id"]) if group_by=="trade" else str(day) if group_by=="day" else
             day.strftime("%Y-%m") if group_by=="month" else day.strftime("%Y") if group_by=="year" else "전체")
        groups[key].append(row)
    items=[]; cumulative=Decimal("0")
    for key,rows in groups.items():
        net=[Decimal(row["net_profit"] or 0) for row in rows]; gross=sum((Decimal(row["gross_profit"] or 0) for row in rows),Decimal("0")); costs=sum((Decimal(row["trading_cost"] or 0) for row in rows),Decimal("0")); total=sum(net,Decimal("0")); cumulative+=total
        equity=Decimal("0"); peak=Decimal("0"); max_drawdown=Decimal("0")
        for value in net:
            equity+=value; peak=max(peak,equity); max_drawdown=min(max_drawdown,equity-peak)
        invested=sum((Decimal(row["entry_price"] or 0) for row in rows),Decimal("0"))
        mfes=[Decimal(row["mfe"]) for row in rows if row["mfe"] is not None]; maes=[Decimal(row["mae"]) for row in rows if row["mae"] is not None]
        item={"period":key,"trade_count":len(rows),"win_count":sum(v>0 for v in net),"loss_count":sum(v<0 for v in net),"flat_count":sum(v==0 for v in net),
          "win_rate":Decimal(sum(v>0 for v in net))/len(rows) if rows else None,"gross_profit":gross,"trading_cost":costs,"net_profit":total,
          "return_rate":sum((Decimal(row["return_rate"] or 0) for row in rows),Decimal("0")),"avg_mfe":sum(mfes,Decimal("0"))/len(mfes) if mfes else None,
          "avg_mae":sum(maes,Decimal("0"))/len(maes) if maes else None,"max_loss":min(net) if net else None,"max_drawdown":max_drawdown,
          "cumulative_net_profit":cumulative,"cumulative_return_rate":sum((Decimal(row["return_rate"] or 0) for row in trades[:trades.index(rows[-1])+1]),Decimal("0")) if rows else None}
        if group_by=="trade": item.update(rows[0])
        items.append(item)
    return {"status":"OK","run_id":run_id,"group_by":group_by,"target":target,"items":items}


def compare_payload(pool):
    with pool.connection() as conn,conn.cursor() as cur:
        cur.execute("""
          WITH targets(code) AS (VALUES ('000660'),('0193T0'),('0197X0')),
          video_runs AS (
            SELECT run_id,created_at,status,initial_capital,parameters
            FROM research_run WHERE parameters->>'strategy_family'='VIDEO_STRATEGY' AND status='COMPLETED'),
          event_stats AS (
            SELECT e.run_id,p.execution_stock_code,count(*) event_count,
              count(*) FILTER(WHERE p.data_status='NORMAL') normal_count,
              count(*) FILTER(WHERE p.data_status='TRADE_PRICE_MISSING') missing_count,
              avg(p.mfe) FILTER(WHERE p.data_status='NORMAL') avg_mfe,
              avg(p.mae) FILTER(WHERE p.data_status='NORMAL') avg_mae
            FROM research_signal_event e JOIN research_video_event_performance p ON p.event_id=e.event_id
            GROUP BY e.run_id,p.execution_stock_code),
          cycle_stats AS (
            SELECT run_id,trade_stock_code,count(*) trade_count,
              count(*) FILTER(WHERE realized_profit>0) win_count,
              count(*) FILTER(WHERE realized_profit<0) loss_count,
              count(*) FILTER(WHERE realized_profit=0) flat_count,
              avg(invested_return_rate) avg_return,sum(gross_realized_profit) gross_profit,
              sum(total_trading_cost) trading_cost,sum(realized_profit) net_profit,
              avg(holding_seconds) avg_holding_time,min(realized_profit) max_loss
            FROM research_trade_cycle WHERE exit_time IS NOT NULL GROUP BY run_id,trade_stock_code)
          SELECT r.run_id,r.created_at,r.status,r.parameters->>'ablation',r.parameters->>'pivot_method',t.code,
            coalesce(c.trade_count,0),coalesce(c.win_count,0),coalesce(c.loss_count,0),coalesce(c.flat_count,0),
            c.win_count::numeric/NULLIF(c.trade_count,0),c.gross_profit,c.trading_cost,c.net_profit,c.avg_return,
            c.avg_holding_time,e.avg_mfe,e.avg_mae,c.max_loss,coalesce(e.missing_count,0),coalesce(e.normal_count,0),
            c.net_profit/NULLIF(r.initial_capital,0)*100
          FROM video_runs r CROSS JOIN targets t
          LEFT JOIN cycle_stats c ON c.run_id=r.run_id AND c.trade_stock_code=t.code
          LEFT JOIN event_stats e ON e.run_id=r.run_id AND e.execution_stock_code=t.code
          ORDER BY c.net_profit DESC NULLS LAST,r.created_at DESC,t.code""")
        names=("run_id","created_at","run_status","ablation","pivot_method","execution_stock_code",
          "trade_count","win_count","loss_count","flat_count","win_rate","gross_profit","trading_cost",
          "net_profit","avg_return","avg_holding_time","avg_mfe","avg_mae","max_loss","missing_count",
          "normal_count","net_return_rate")
        return {"status":"OK","items":[dict(zip(names,row)) for row in cur.fetchall()]}


def event_analysis_payload(pool,run_id):
    with pool.connection() as conn,conn.cursor() as cur:
        if not run_id:
            return {"status":"ERROR","message":"run_id가 필요합니다.","items":[]}
        UUID(str(run_id))
        cur.execute("""SELECT e.signal_type,p.execution_stock_code,count(*),
          avg(p.return_1m),avg(p.return_3m),avg(p.return_5m),avg(p.return_10m),avg(p.return_20m),avg(p.return_30m),avg(p.mfe),avg(p.mae)
          FROM research_signal_event e JOIN research_video_event_performance p ON p.event_id=e.event_id
          WHERE e.run_id=%s AND p.data_status='NORMAL'
          GROUP BY e.signal_type,p.execution_stock_code ORDER BY e.signal_type,p.execution_stock_code""",(run_id,))
        names=("event_type","execution_stock_code","event_count","return_1m","return_3m","return_5m","return_10m","return_20m","return_30m","mfe","mae")
        return {"status":"OK","run_id":run_id,"items":[dict(zip(names,row)) for row in cur.fetchall()]}
