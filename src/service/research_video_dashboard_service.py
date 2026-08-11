"""Read-only VIDEO_STRATEGY dashboard queries, isolated from legacy payloads."""
from __future__ import annotations
from uuid import UUID

def runs_payload(pool):
    with pool.connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT r.run_id,r.created_at,r.start_date,r.end_date,r.status,r.parameters,
          count(DISTINCT e.event_id),count(DISTINCT c.cycle_id)
          FROM research_run r LEFT JOIN research_signal_event e ON e.run_id=r.run_id
          LEFT JOIN research_trade_cycle c ON c.run_id=r.run_id
          WHERE r.parameters->>'strategy_family'='VIDEO_STRATEGY'
          GROUP BY r.run_id ORDER BY r.created_at DESC LIMIT 100""")
        return {"status":"OK","runs":[dict(zip(("run_id","created_at","start_date","end_date","run_status","parameters","event_count","cycle_count"),row)) for row in cur.fetchall()]}

def replay_payload(pool,run_id):
    UUID(str(run_id))
    with pool.connection() as conn,conn.cursor() as cur:
        cur.execute("SELECT parameters,start_date,end_date,status FROM research_run WHERE run_id=%s AND parameters->>'strategy_family'='VIDEO_STRATEGY'",(run_id,)); run=cur.fetchone()
        if run is None:return {"status":"NOT_FOUND"}
        cur.execute("""SELECT f.observation_time,f.price,f.ma20,f.ma10_direction,f.feature_detail,b.open_price,b.high_price,b.low_price,b.close_price,b.volume
          FROM research_feature f LEFT JOIN raw_stock_minute b ON b.stock_code=f.stock_code AND b.bar_time=f.observation_time AND b.data_source='KIS' AND b.market_code='KOSPI' AND b.trading_venue='INTEGRATED' AND b.collect_cycle='1MIN'
          WHERE f.run_id=%s AND f.stock_code='000660' ORDER BY f.observation_time""",(run_id,)); features=cur.fetchall()
        cur.execute("""SELECT e.event_id,e.signal_time,e.signal_type,e.direction,e.signal_price,e.event_detail
          FROM research_signal_event e WHERE e.run_id=%s ORDER BY e.signal_time,e.event_id""",(run_id,)); events=cur.fetchall()
        cur.execute("""SELECT p.event_id,p.execution_stock_code,p.execution_direction,p.trade_price,p.return_1m,p.return_3m,p.return_5m,p.return_10m,p.return_20m,p.return_30m,p.mfe,p.mae,p.data_status
          FROM research_video_event_performance p JOIN research_signal_event e ON e.event_id=p.event_id WHERE e.run_id=%s ORDER BY p.event_id,p.execution_stock_code""",(run_id,)); perf=cur.fetchall()
        cur.execute("""SELECT trade_stock_code,direction,entry_time,entry_price,exit_time,exit_price,realized_profit,invested_return_rate,data_status
          FROM research_trade_cycle WHERE run_id=%s ORDER BY entry_time,trade_stock_code""",(run_id,)); cycles=cur.fetchall()
    cols=lambda names,rows:[dict(zip(names,row)) for row in rows]
    return {"status":"OK","run_id":run_id,"parameters":run[0],"start_date":run[1],"end_date":run[2],"run_status":run[3],
      "features":cols(("time","price","sma20","sma20_direction","detail","open","high","low","close","volume"),features),
      "events":cols(("event_id","time","event_type","direction","price","detail"),events),
      "performance":cols(("event_id","execution_stock_code","execution_direction","trade_price","return_1m","return_3m","return_5m","return_10m","return_20m","return_30m","mfe","mae","data_status"),perf),
      "cycles":cols(("execution_stock_code","direction","entry_time","entry_price","exit_time","exit_price","realized_profit","return_rate","data_status"),cycles)}

def compare_payload(pool):
    with pool.connection() as conn,conn.cursor() as cur:
        cur.execute("""WITH event_stats AS (SELECT e.run_id,p.execution_stock_code,count(DISTINCT e.event_id) event_count,count(*) FILTER(WHERE p.data_status='NORMAL') normal_count,avg(p.return_5m) avg_return_5m,percentile_cont(.5) WITHIN GROUP(ORDER BY p.return_5m) median_return_5m,avg(p.mfe) avg_mfe,avg(p.mae) avg_mae FROM research_signal_event e JOIN research_video_event_performance p ON p.event_id=e.event_id GROUP BY e.run_id,p.execution_stock_code),
          cycle_rows AS (SELECT run_id,trade_stock_code,realized_profit,invested_return_rate,holding_seconds,sum(realized_profit) OVER(PARTITION BY run_id,trade_stock_code ORDER BY exit_time,cycle_id) equity FROM research_trade_cycle WHERE exit_time IS NOT NULL),
          cycle_stats AS (SELECT run_id,trade_stock_code,count(*) trade_count,count(*) FILTER(WHERE realized_profit>0)::numeric/NULLIF(count(*),0) win_rate,avg(invested_return_rate) avg_return,sum(realized_profit) FILTER(WHERE realized_profit>0)/NULLIF(abs(sum(realized_profit) FILTER(WHERE realized_profit<0)),0) profit_factor,avg(realized_profit) expectancy,min(equity) max_drawdown,avg(holding_seconds) avg_holding_time FROM cycle_rows GROUP BY run_id,trade_stock_code)
          SELECT r.run_id,r.parameters->>'ablation',r.parameters->>'pivot_method',s.execution_stock_code,s.event_count,s.normal_count,s.avg_return_5m,s.median_return_5m,s.avg_mfe,s.avg_mae,coalesce(c.trade_count,0),c.win_rate,c.avg_return,c.profit_factor,c.expectancy,c.max_drawdown,c.avg_holding_time
          FROM research_run r JOIN event_stats s ON s.run_id=r.run_id LEFT JOIN cycle_stats c ON c.run_id=r.run_id AND c.trade_stock_code=s.execution_stock_code
          WHERE r.parameters->>'strategy_family'='VIDEO_STRATEGY' ORDER BY r.created_at DESC,s.execution_stock_code""")
        names=("run_id","ablation","pivot_method","execution_stock_code","event_count","normal_count","avg_return_5m","median_return_5m","avg_mfe","avg_mae","trade_count","win_rate","avg_return","profit_factor","expectancy","max_drawdown","avg_holding_time")
        return {"status":"OK","items":[dict(zip(names,row)) for row in cur.fetchall()]}

def event_analysis_payload(pool,run_id):
    UUID(str(run_id))
    with pool.connection() as conn,conn.cursor() as cur:
        cur.execute("""SELECT e.signal_type,p.execution_stock_code,count(*),
          avg(p.return_1m),avg(p.return_3m),avg(p.return_5m),avg(p.return_10m),avg(p.return_20m),avg(p.return_30m),avg(p.mfe),avg(p.mae)
          FROM research_signal_event e JOIN research_video_event_performance p ON p.event_id=e.event_id
          WHERE e.run_id=%s AND p.data_status='NORMAL' GROUP BY e.signal_type,p.execution_stock_code ORDER BY e.signal_type,p.execution_stock_code""",(run_id,))
        names=("event_type","execution_stock_code","event_count","return_1m","return_3m","return_5m","return_10m","return_20m","return_30m","mfe","mae")
        return {"status":"OK","items":[dict(zip(names,row)) for row in cur.fetchall()]}
