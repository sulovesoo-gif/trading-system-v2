"""Idempotent compound/fixed-10M end-of-day metrics and candidates."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

PERIODS=("cumulative","recent20","month","week","day")


def _return(profit: Decimal, initial: Decimal) -> Decimal:
    return profit/initial if initial else Decimal(0)


def rank_records(records, model: str = "compound"):
    ranks={period:{} for period in PERIODS}
    profits_key=f"{model}_profits"
    for period in PERIODS:
        for filter_code in ("REAL_F1","REAL_F2","REAL_F3"):
            group=[row for row in records if row["filter"]==filter_code]
            group.sort(key=lambda row:(-_return(row[profits_key][period],row["initial"]),
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


def _mdd(initial: Decimal, pnls: list[Decimal]) -> Decimal:
    capital=peak=initial; drawdown=Decimal(0)
    for pnl in pnls:
        capital+=pnl; peak=max(peak,capital)
        if peak: drawdown=min(drawdown,(capital-peak)/peak)
    return drawdown


class MinuteMaRealPaperEod:
    def __init__(self,pool) -> None: self.pool=pool

    def refresh(self,snapshot_date:date) -> tuple[int,int]:
        with self.pool.connection() as connection,connection.cursor() as cursor:
            cursor.execute("""SELECT v.real_variant_id,v.strategy_id,v.signal_code,v.filter_code,
              v.initial_capital,c.current_realized_capital
              FROM minute_ma_real_variant v JOIN minute_ma_real_capital_epoch c
                ON c.real_variant_id=v.real_variant_id AND c.paper_epoch=v.paper_epoch
               AND c.ended_at IS NULL
              WHERE v.enabled AND v.effective_from<=%s
                AND (v.effective_to IS NULL OR v.effective_to>=%s)""",
              (snapshot_date,snapshot_date)); variants=cursor.fetchall()
            cursor.execute("""SELECT real_variant_id,exit_execution_time::date,
              compound_realized_pnl,fixed_realized_pnl
              FROM minute_ma_real_paper_trade WHERE lifecycle_status='CLOSED'
               AND exit_execution_time::date<=%s
              ORDER BY real_variant_id,settlement_time,real_paper_trade_id""",
              (snapshot_date,)); trade_rows=cursor.fetchall()
            trades=defaultdict(list)
            for variant_id,day,compound_pnl,fixed_pnl in trade_rows:
                trades[int(variant_id)].append(
                    (day,Decimal(str(compound_pnl)),Decimal(str(fixed_pnl))))
            cursor.execute("""SELECT min(d) FROM (SELECT DISTINCT bar_time::date d
              FROM raw_stock_minute WHERE stock_code IN ('000660','005930')
                AND data_source='KIS' AND trading_venue='KRX' AND collect_cycle='1MIN'
                AND bar_time::date<=%s ORDER BY d DESC LIMIT 20) x""",(snapshot_date,))
            recent_start=cursor.fetchone()[0] or snapshot_date
            week_start=snapshot_date-timedelta(days=snapshot_date.weekday())
            month_start=snapshot_date.replace(day=1)
            records=[]
            for variant_id,strategy_id,signal_code,filter_code,initial,current in variants:
                initial=Decimal(str(initial)); rows=trades[int(variant_id)]
                def profits(index):
                    def since(start): return sum((row[index] for row in rows if row[0]>=start),Decimal(0))
                    return {"cumulative":sum((row[index] for row in rows),Decimal(0)),
                            "recent20":since(recent_start),"month":since(month_start),
                            "week":since(week_start),"day":since(snapshot_date)}
                compound_profits=profits(1); fixed_profits=profits(2)
                records.append({"variant":int(variant_id),"strategy":str(strategy_id),
                  "signal":str(signal_code),"filter":str(filter_code),"initial":initial,
                  "current":Decimal(str(current)),"count":len(rows),
                  "win":sum(1 for _,pnl,_ in rows if pnl>0),
                  "compound_profits":compound_profits,"fixed_profits":fixed_profits,
                  "compound_mdd":_mdd(initial,[row[1] for row in rows]),
                  "fixed_mdd":_mdd(initial,[row[2] for row in rows])})
            compound_ranks=rank_records(records,"compound")
            fixed_ranks=rank_records(records,"fixed")

            cursor.execute("SELECT max(snapshot_date) FROM minute_ma_real_daily_snapshot WHERE snapshot_date<%s",
                           (snapshot_date,)); previous_day=cursor.fetchone()[0]
            def prior(day,model,period):
                if day is None: return {}
                rank_col=f"{model}_{period}_rank"
                profit_col=f"{model}_cumulative_profit"
                cursor.execute(f"SELECT real_variant_id,{rank_col},{profit_col} "
                               "FROM minute_ma_real_daily_snapshot WHERE snapshot_date=%s",(day,))
                return {int(r[0]):(int(r[1]),Decimal(str(r[2]))) for r in cursor.fetchall()}
            cursor.execute("SELECT max(snapshot_date) FROM minute_ma_real_daily_snapshot WHERE snapshot_date<%s",
                           (week_start,)); previous_week_day=cursor.fetchone()[0]
            cursor.execute("SELECT max(snapshot_date) FROM minute_ma_real_daily_snapshot WHERE snapshot_date<%s",
                           (month_start,)); previous_month_day=cursor.fetchone()[0]
            priors={}
            for model in ("compound","fixed"):
                priors[(model,"day")]=prior(previous_day,model,"cumulative")
                priors[(model,"week")]=prior(previous_week_day,model,"week")
                priors[(model,"month")]=prior(previous_month_day,model,"month")

            cursor.execute("DELETE FROM minute_ma_real_daily_snapshot WHERE snapshot_date=%s",(snapshot_date,))
            base_columns=["snapshot_date","real_variant_id","strategy_id","signal_code","filter_code",
                          "compound_current_capital","trade_count","win_count","win_rate",
                          "compound_average_net_profit","fixed_average_net_profit",
                          "compound_max_drawdown","fixed_max_drawdown"]
            metric_columns=[]
            for model in ("compound","fixed"):
                for period in PERIODS:
                    metric_columns.extend([f"{model}_{period}_profit",f"{model}_{period}_return"])
                metric_columns.extend(f"{model}_{period}_rank" for period in PERIODS)
                for delta in ("day","week","month"):
                    metric_columns.extend([f"{model}_rank_delta_{delta}",
                                           f"{model}_profit_delta_{delta}"])
            columns=base_columns+metric_columns
            placeholders=','.join(['%s']*len(columns))
            insert_sql=f"INSERT INTO minute_ma_real_daily_snapshot({','.join(columns)}) VALUES({placeholders})"
            for row in records:
                count=row["count"]; win=row["win"]
                values=[snapshot_date,row["variant"],row["strategy"],row["signal"],row["filter"],
                        row["current"],count,win,Decimal(win)/count if count else Decimal(0),
                        row["compound_profits"]["cumulative"]/count if count else Decimal(0),
                        row["fixed_profits"]["cumulative"]/count if count else Decimal(0),
                        row["compound_mdd"],row["fixed_mdd"]]
                for model,ranks in (("compound",compound_ranks),("fixed",fixed_ranks)):
                    profits=row[f"{model}_profits"]
                    for period in PERIODS:
                        values.extend([profits[period],_return(profits[period],row["initial"])])
                    values.extend(ranks[period][row["variant"]] for period in PERIODS)
                    for delta in ("day","week","month"):
                        old=priors[(model,delta)].get(row["variant"])
                        rank_period="cumulative" if delta=="day" else delta
                        values.extend([(old[0]-ranks[rank_period][row["variant"]]) if old else None,
                                       (profits["cumulative"]-old[1]) if old else None])
                cursor.execute(insert_sql,values)

            reasons_by_strategy,reasons=candidate_reasons(records,compound_ranks)
            cursor.execute("""SELECT real_variant_id FROM minute_ma_real_candidate_snapshot
              WHERE snapshot_date=(SELECT max(snapshot_date) FROM minute_ma_real_candidate_snapshot
                                    WHERE snapshot_date<%s)
                AND candidate_state IN ('ENTERED','STAYED')""",(snapshot_date,))
            prior_candidates={int(r[0]) for r in cursor.fetchall()}
            cursor.execute("DELETE FROM minute_ma_real_candidate_snapshot WHERE snapshot_date=%s",(snapshot_date,))
            by_id={row["variant"]:row for row in records}
            for variant_id in set(reasons)|prior_candidates:
                current=variant_id in reasons; was_prior=variant_id in prior_candidates
                state='STAYED' if current and was_prior else ('ENTERED' if current else 'EXITED')
                row=by_id[variant_id]
                cursor.execute("""INSERT INTO minute_ma_real_candidate_snapshot(
                  snapshot_date,real_variant_id,strategy_id,filter_code,inclusion_reasons,
                  prior_candidate,candidate_state) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                  (snapshot_date,variant_id,row["strategy"],row["filter"],
                   reasons.get(variant_id,[]),was_prior,state))
            connection.commit()
        return len(records),len(reasons_by_strategy)
