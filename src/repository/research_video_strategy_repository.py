"""Persistence boundary used only by VIDEO_STRATEGY research replay."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
import json

from psycopg.types.json import Jsonb

from src.analysis.feature.sma_feature import MinuteBar


class ResearchVideoStrategyRepository:
    def __init__(self, pool) -> None: self.pool=pool

    def minute_rows(self, stock_code: str, start_date: date, end_date: date):
        venue="INTEGRATED" if stock_code in {"000660","005930"} else "KRX"
        start=datetime.combine(start_date,datetime.min.time()); end=datetime.combine(end_date,datetime.max.time())
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT bar_time,open_price,high_price,low_price,close_price,COALESCE(volume,0)
              FROM raw_stock_minute WHERE stock_code=%s AND data_source='KIS' AND market_code='KOSPI'
               AND trading_venue=%s AND collect_cycle='1MIN' AND bar_time >= %s AND bar_time <= %s
               AND open_price IS NOT NULL AND high_price IS NOT NULL AND low_price IS NOT NULL AND close_price IS NOT NULL
              ORDER BY bar_time""",(stock_code,venue,start,end))
            return [(MinuteBar(row[0],Decimal(row[1]),Decimal(row[2]),Decimal(row[3]),Decimal(row[4])),Decimal(row[5])) for row in cur.fetchall()]

    def create_run(self,run_id:UUID,start_date:date,end_date:date,parameters:dict):
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_run(run_id,start_date,end_date,status,cost_policy_version,fee_rate,slippage_rate,initial_capital,parameters)
              VALUES(%s,%s,%s,'RUNNING',%s,%s,%s,%s,%s)""",(run_id,start_date,end_date,parameters["cost_policy_version"],parameters["fee_rate"],parameters["slippage_rate"],parameters["capital_policy"].get("initial_capital","10000000"),Jsonb(parameters)))

    def finish_run(self,run_id:UUID,status:str):
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur: cur.execute("UPDATE research_run SET status=%s WHERE run_id=%s",(status,run_id))

    def save_features(self,run_id:UUID,stock_code:str,features):
        values=[(run_id,f.bar.bar_time.date(),stock_code,f.bar.bar_time,f.bar.close_price,f.sma20,f.sma20_direction,f.data_status,"VIDEO_STRATEGY","V1",Jsonb(f.detail())) for f in features]
        if not values:return
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.executemany("""INSERT INTO research_feature(run_id,trading_date,stock_code,observation_code,observation_time,price,ma20,ma10_direction,data_status,strategy_family,strategy_version,feature_detail)
              VALUES(%s,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(run_id,stock_code,observation_code,observation_time) DO NOTHING""",values)

    def save_events(self,run_id:UUID,stock_code:str,events):
        result=[]
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            for e in events:
                cur.execute("""INSERT INTO research_signal_event(run_id,trading_date,signal_source_stock_code,observation_code,signal_time,strategy_code,signal_type,direction,signal_price,ma10,ma10_direction,pending_yn,session_code,data_status,strategy_family,strategy_version,event_detail)
                  VALUES(%s,%s,%s,'COMPLETE',%s,'VIDEO_STRATEGY_V1',%s,%s,%s,%s,%s,'N',%s,%s,'VIDEO_STRATEGY','V1',%s)
                  ON CONFLICT(run_id,signal_source_stock_code,observation_code,signal_time,strategy_code,signal_type,direction)
                  DO UPDATE SET event_detail=EXCLUDED.event_detail RETURNING event_id""",
                  (run_id,e.at.date(),stock_code,e.at,e.event_type,e.direction,e.price,e.feature.sma20,e.feature.sma20_direction,_session(e.at),e.feature.data_status,Jsonb(json.loads(json.dumps(dict(e.detail),default=str)))))
                result.append((cur.fetchone()[0],e))
        return result

    def save_event_performance(self,event_id:int,stock_code:str,event,measurement:dict):
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_video_event_performance(event_id,execution_stock_code,signal_direction,execution_direction,event_price,trade_price,return_1m,return_3m,return_5m,return_10m,return_20m,return_30m,mfe,mae,data_status,detail)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(event_id,execution_stock_code) DO NOTHING""",(event_id,stock_code,event.direction,measurement.get("execution_direction") or "LONG",event.price,measurement.get("trade_price"),*(measurement.get(f"return_{n}m") for n in (1,3,5,10,20,30)),measurement.get("mfe"),measurement.get("mae"),measurement["data_status"],Jsonb({"exact_timestamp":True})))

    def save_cycle(self,run_id:UUID,source:str,target:str,entry,exit_,measurement:dict,parameters:dict):
        if measurement["data_status"]!="NORMAL" or measurement.get("trade_price") is None:return None
        direction=measurement["execution_direction"]; entry_price=measurement["trade_price"]
        # Exit must also be exact timestamp; caller supplies it in measurement.
        exit_price=measurement.get("exit_price")
        if exit_price is None:return None
        capital=Decimal(parameters["capital_policy"].get("initial_capital","10000000")); qty=int(capital/entry_price)
        sign=Decimal("-1") if direction=="VIRTUAL_SHORT" else Decimal("1")
        gross=(exit_price-entry_price)*qty*sign; buy=entry_price*qty*Decimal(parameters["fee_rate"]); sell=exit_price*qty*Decimal(parameters["fee_rate"]); tax=exit_price*qty*Decimal(parameters["sell_tax_rate"]); net=gross-buy-sell-tax
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_trade_cycle(run_id,trading_date,trade_stock_code,signal_source_stock_code,exit_signal_source_stock_code,strategy_code,observation_code,direction,entry_signal_time,entry_confirm_time,entry_time,entry_price,exit_signal_time,exit_time,exit_price,exit_type,quantity,invested_amount,gross_realized_profit,buy_fee,sell_fee,sell_tax,total_trading_cost,realized_profit,invested_return_rate,capital_return_rate,holding_seconds,data_status)
              VALUES(%s,%s,%s,%s,%s,'VIDEO_STRATEGY_V1','COMPLETE',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(run_id,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,entry_time) DO NOTHING RETURNING cycle_id""",
              (run_id,entry.at.date(),target,source,source,"SHORT" if direction=="VIRTUAL_SHORT" else "LONG",entry.at,entry.at,entry.at,entry_price,exit_.at,exit_.at,exit_price,exit_.event_type,qty,entry_price*qty,gross,buy,sell,tax,buy+sell+tax,net,net/(entry_price*qty)*100 if qty else 0,net/capital*100,int((exit_.at-entry.at).total_seconds()),"NORMAL"))
            row=cur.fetchone(); return row[0] if row else None

    def summary(self,run_id:UUID):
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT (SELECT count(*) FROM research_feature WHERE run_id=%s),
              (SELECT count(*) FROM research_signal_event WHERE run_id=%s),
              (SELECT count(*) FROM research_trade_cycle WHERE run_id=%s),
              (SELECT count(*) FROM research_video_event_performance p JOIN research_signal_event e ON e.event_id=p.event_id WHERE e.run_id=%s AND p.data_status='TRADE_PRICE_MISSING')""",(run_id,run_id,run_id,run_id))
            return cur.fetchone()

    def save_daily_performance(self,run_id:UUID):
        """Insert aggregates for this new run only; never replace legacy research."""
        with self.pool.connection() as conn,conn.transaction(),conn.cursor() as cur:
            cur.execute("""INSERT INTO research_performance_daily(run_id,trading_date,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction,session_code,closed_count,win_count,loss_count,flat_count,realized_profit,invested_amount,invested_return_rate,capital_return_rate,avg_trade_return_rate,avg_holding_seconds,signal_exit_profit,session_close_profit)
              SELECT c.run_id,c.trading_date,c.trade_stock_code,c.signal_source_stock_code,c.strategy_code,c.observation_code,c.direction,'ALL',count(*),count(*) FILTER(WHERE realized_profit>0),count(*) FILTER(WHERE realized_profit<0),count(*) FILTER(WHERE realized_profit=0),sum(realized_profit),sum(invested_amount),sum(realized_profit)/NULLIF(sum(invested_amount),0)*100,sum(realized_profit)/NULLIF(max(r.initial_capital),0)*100,avg(invested_return_rate),avg(holding_seconds),coalesce(sum(realized_profit) FILTER(WHERE exit_type<>'SESSION_CLOSE'),0),coalesce(sum(realized_profit) FILTER(WHERE exit_type='SESSION_CLOSE'),0)
              FROM research_trade_cycle c JOIN research_run r ON r.run_id=c.run_id WHERE c.run_id=%s AND c.exit_time IS NOT NULL
              GROUP BY c.run_id,c.trading_date,c.trade_stock_code,c.signal_source_stock_code,c.strategy_code,c.observation_code,c.direction
              ON CONFLICT DO NOTHING""",(run_id,))


def _session(value):
    hm=(value.hour,value.minute)
    return "NXT_PREMARKET" if (8,0)<=hm<=(8,49) else "KRX_REGULAR" if (9,0)<=hm<=(15,19) else "NXT_AFTERMARKET" if (15,40)<=hm<=(20,0) else None
