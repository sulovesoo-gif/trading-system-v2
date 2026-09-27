"""Idempotent end-of-day metrics/ranks/candidate snapshots."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal


PERIODS=("cumulative","recent20","month","week","day")


def _return(profit: Decimal, initial: Decimal) -> Decimal:
    return profit/initial if initial else Decimal(0)


def rank_records(records):
    ranks={period:{} for period in PERIODS}
    for period in PERIODS:
        for filter_code in ("REAL_F1","REAL_F2","REAL_F3"):
            group=[row for row in records if row["filter"]==filter_code]
            group.sort(key=lambda row:(-_return(row["profits"][period],row["initial"]),
                                       row["strategy"]))
            for rank,row in enumerate(group,1): ranks[period][row["variant"]]=rank
    return ranks


def candidate_reasons(records,ranks):
    by_strategy:dict[str,set[str]]=defaultdict(set)
    for row in records:
        for period,label in (("cumulative","CUM"),("recent20","R20"),("month","MONTH")):
            if ranks[period][row["variant"]]<=100:
                by_strategy[row["strategy"]].add(
                    f"{label}_{row['filter'].replace('REAL_','')}_TOP100")
    return by_strategy,{row["variant"]:sorted(by_strategy[row["strategy"]])
                       for row in records if row["strategy"] in by_strategy}


class MinuteMaRealPaperEod:
    def __init__(self,pool) -> None: self.pool=pool

    def refresh(self,snapshot_date:date) -> tuple[int,int]:
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("""SELECT v.real_variant_id,v.strategy_id,v.signal_code,v.filter_code,v.k_mode,
              v.initial_capital,COALESCE(SUM(s.current_capital),v.initial_capital)
              FROM minute_ma_real_variant v LEFT JOIN minute_ma_real_paper_slot s
                ON s.real_variant_id=v.real_variant_id AND s.paper_epoch=v.paper_epoch
              WHERE v.enabled AND v.effective_from<=%s AND (v.effective_to IS NULL OR v.effective_to>=%s)
              GROUP BY v.real_variant_id""",(snapshot_date,snapshot_date)); variants=cursor.fetchall()
            cursor.execute("""SELECT real_variant_id,exit_execution_time::date,realized_pnl,capital_after
              FROM minute_ma_real_paper_trade WHERE lifecycle_status='CLOSED'
               AND exit_execution_time::date<=%s ORDER BY real_variant_id,exit_execution_time,real_paper_trade_id""",
              (snapshot_date,)); trade_rows=cursor.fetchall()
            trades:dict[int,list[tuple[date,Decimal,Decimal]]]=defaultdict(list)
            for variant_id,day,pnl,capital in trade_rows:
                trades[int(variant_id)].append((day,Decimal(str(pnl)),Decimal(str(capital))))
            # Trading dates, including zero-trade dates, define recent 20.
            cursor.execute("""SELECT min(d) FROM (SELECT DISTINCT bar_time::date d
              FROM raw_stock_minute WHERE stock_code IN ('000660','005930')
                AND data_source='KIS' AND trading_venue='KRX' AND collect_cycle='1MIN'
                AND bar_time::date<=%s ORDER BY d DESC LIMIT 20) x""",(snapshot_date,))
            recent_start=cursor.fetchone()[0] or snapshot_date
            week_start=snapshot_date-timedelta(days=snapshot_date.weekday())
            month_start=snapshot_date.replace(day=1)
            records=[]
            for variant_id,strategy_id,signal_code,filter_code,k,initial,current in variants:
                initial=Decimal(str(initial)); rows=trades[int(variant_id)]
                def profit_since(start): return sum((pnl for day,pnl,_ in rows if day>=start),Decimal(0))
                cumulative=sum((pnl for _,pnl,_ in rows),Decimal(0))
                profits={"cumulative":cumulative,"recent20":profit_since(recent_start),
                         "month":profit_since(month_start),"week":profit_since(week_start),
                         "day":profit_since(snapshot_date)}
                running=initial
                capitals=[initial]
                for _,pnl,_ in rows:
                    running+=pnl; capitals.append(running)
                peak=capitals[0]; mdd=Decimal(0)
                for capital in capitals:
                    peak=max(peak,capital)
                    if peak: mdd=min(mdd,(capital-peak)/peak)
                win=sum(1 for _,pnl,_ in rows if pnl>0); count=len(rows)
                records.append({"variant":int(variant_id),"strategy":str(strategy_id),
                  "signal":str(signal_code),"filter":str(filter_code),"k":int(k),
                  "initial":initial,"current":Decimal(str(current)),"count":count,"win":win,
                  "win_rate":Decimal(win)/count if count else Decimal(0),
                  "average":cumulative/count if count else Decimal(0),"mdd":mdd,"profits":profits})
            ranks=rank_records(records)
            cursor.execute("SELECT max(snapshot_date) FROM minute_ma_real_daily_snapshot WHERE snapshot_date<%s",
                           (snapshot_date,)); previous_day=cursor.fetchone()[0]
            previous={}
            if previous_day:
                cursor.execute("""SELECT real_variant_id,cumulative_rank,cumulative_profit FROM minute_ma_real_daily_snapshot
                  WHERE snapshot_date=%s""",(previous_day,)); previous={int(r[0]):(int(r[1]),Decimal(str(r[2]))) for r in cursor.fetchall()}
            def prior_period(before:date,rank_column:str):
                cursor.execute(f"""SELECT real_variant_id,{rank_column},cumulative_profit
                  FROM minute_ma_real_daily_snapshot WHERE snapshot_date=(
                    SELECT max(snapshot_date) FROM minute_ma_real_daily_snapshot WHERE snapshot_date<%s)""",
                  (before,))
                return {int(r[0]):(int(r[1]),Decimal(str(r[2]))) for r in cursor.fetchall()}
            previous_week=prior_period(week_start,"week_rank")
            previous_month=prior_period(month_start,"month_rank")
            cursor.execute("DELETE FROM minute_ma_real_daily_snapshot WHERE snapshot_date=%s",(snapshot_date,))
            for row in records:
                prior=previous.get(row["variant"])
                prior_week=previous_week.get(row["variant"]); prior_month=previous_month.get(row["variant"])
                cursor.execute("""INSERT INTO minute_ma_real_daily_snapshot(
                  snapshot_date,real_variant_id,strategy_id,signal_code,filter_code,k_mode,current_capital,
                  trade_count,win_count,win_rate,average_net_profit,max_drawdown,cumulative_profit,cumulative_return,
                  recent20_profit,recent20_return,month_profit,month_return,week_profit,week_return,day_profit,day_return,
                  cumulative_rank,recent20_rank,month_rank,week_rank,day_rank,rank_delta_day,rank_delta_week,
                  rank_delta_month,profit_delta_day,profit_delta_week,profit_delta_month)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                         %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (snapshot_date,row["variant"],row["strategy"],row["signal"],row["filter"],row["k"],
                   row["current"],row["count"],row["win"],row["win_rate"],row["average"],row["mdd"],
                   row["profits"]["cumulative"],_return(row["profits"]["cumulative"],row["initial"]),
                   row["profits"]["recent20"],_return(row["profits"]["recent20"],row["initial"]),
                   row["profits"]["month"],_return(row["profits"]["month"],row["initial"]),
                   row["profits"]["week"],_return(row["profits"]["week"],row["initial"]),
                   row["profits"]["day"],_return(row["profits"]["day"],row["initial"]),
                   ranks["cumulative"][row["variant"]],ranks["recent20"][row["variant"]],
                   ranks["month"][row["variant"]],ranks["week"][row["variant"]],ranks["day"][row["variant"]],
                   (prior[0]-ranks["cumulative"][row["variant"]]) if prior else None,
                   (prior_week[0]-ranks["week"][row["variant"]]) if prior_week else None,
                   (prior_month[0]-ranks["month"][row["variant"]]) if prior_month else None,
                   (row["profits"]["cumulative"]-prior[1]) if prior else None,
                   (row["profits"]["cumulative"]-prior_week[1]) if prior_week else None,
                   (row["profits"]["cumulative"]-prior_month[1]) if prior_month else None))
            # Current candidates are the union of cumulative/R20/month top 100 per filter.
            # Candidate identity is strategy_id.  All three filter rows expose
            # the same union reasons for an included strategy.
            reasons_by_strategy,reasons=candidate_reasons(records,ranks)
            cursor.execute("""SELECT real_variant_id FROM minute_ma_real_candidate_snapshot
              WHERE snapshot_date=(SELECT max(snapshot_date) FROM minute_ma_real_candidate_snapshot WHERE snapshot_date<%s)
                AND candidate_state IN ('ENTERED','STAYED')""",(snapshot_date,)); prior_candidates={int(r[0]) for r in cursor.fetchall()}
            cursor.execute("DELETE FROM minute_ma_real_candidate_snapshot WHERE snapshot_date=%s",(snapshot_date,))
            by_id={row["variant"]:row for row in records}
            for variant_id in set(reasons)|prior_candidates:
                current=variant_id in reasons; prior=variant_id in prior_candidates
                state='STAYED' if current and prior else ('ENTERED' if current else 'EXITED')
                row=by_id[variant_id]
                cursor.execute("""INSERT INTO minute_ma_real_candidate_snapshot(
                  snapshot_date,real_variant_id,strategy_id,filter_code,inclusion_reasons,prior_candidate,candidate_state)
                  VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                  (snapshot_date,variant_id,row["strategy"],row["filter"],reasons.get(variant_id,[]),prior,state))
            connection.commit()
        return len(records),len(reasons_by_strategy)
