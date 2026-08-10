"""Persistence for reproducible research runs only; never a live trading repository."""
from __future__ import annotations

from datetime import date
from uuid import UUID
from psycopg.types.json import Jsonb


class ResearchRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def create_run(self, *, run_id: UUID, start_date: date, end_date: date, parameters: dict, status: str = "RUNNING") -> None:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_run(run_id,start_date,end_date,status,cost_policy_version,fee_rate,slippage_rate,parameters)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING""", (run_id, start_date, end_date, status,
                        parameters.get("cost_policy_version", "UNSPECIFIED"), parameters.get("fee_rate", 0), parameters.get("slippage_rate", 0), Jsonb(parameters)))

    def finish_run(self, run_id: UUID, status: str) -> None:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("UPDATE research_run SET status=%s WHERE run_id=%s", (status, run_id))

    def save_feature(self, *, run_id: UUID, stock_code: str, feature, ma10_direction: str | None, data_status: str = "NORMAL") -> None:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_feature(run_id,trading_date,stock_code,observation_code,observation_time,price,ma3,ma5,ma10,ma20,ma10_direction,data_status)
            VALUES (%s,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id,stock_code,observation_code,observation_time) DO NOTHING""",
                        (run_id, feature.bar.bar_time.date(), stock_code, feature.bar.bar_time, feature.value,
                         feature.ma_short, feature.ma_mid, feature.ma_long, feature.ma20, ma10_direction, data_status))

    def save_signal(self, *, run_id: UUID, stock_code: str, strategy_code: str, signal, ma10_direction: str | None, pending: bool, confirm_time, session_code: str | None, data_status: str = "NORMAL") -> None:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_signal_event(run_id,trading_date,signal_source_stock_code,observation_code,signal_time,strategy_code,signal_type,direction,signal_price,ma3,ma5,ma10,ma10_direction,pending_yn,pending_started_at,confirm_time,session_code,data_status)
            VALUES (%s,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id,signal_source_stock_code,observation_code,signal_time,strategy_code,signal_type,direction) DO NOTHING""",
                        (run_id, signal.at.date(), stock_code, signal.at, strategy_code, signal.signal_type, signal.direction,
                         signal.feature.value, signal.feature.ma_short, signal.feature.ma_mid, signal.feature.ma_long, ma10_direction,
                         "Y" if pending else "N", signal.at if pending else None, confirm_time, session_code, data_status))

    def save_cycle(self, *, run_id: UUID, trade_stock_code: str, signal_source_stock_code: str, cycle,
                   exit_signal_source_stock_code: str | None = None) -> int:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_trade_cycle(run_id,trading_date,trade_stock_code,signal_source_stock_code,exit_signal_source_stock_code,strategy_code,observation_code,direction,entry_signal_time,entry_confirm_time,entry_time,entry_price,exit_signal_time,exit_time,exit_price,exit_type,quantity,invested_amount,realized_profit,invested_return_rate,capital_return_rate,holding_seconds,data_status)
            VALUES (%s,%s,%s,%s,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,entry_time)
            DO UPDATE SET exit_time=EXCLUDED.exit_time,exit_price=EXCLUDED.exit_price,exit_type=EXCLUDED.exit_type,realized_profit=EXCLUDED.realized_profit
            RETURNING cycle_id""", (run_id, cycle.entry_confirm_time.date(), trade_stock_code, signal_source_stock_code, exit_signal_source_stock_code or signal_source_stock_code,
              cycle.strategy_code, cycle.direction, cycle.entry_signal_time, cycle.entry_confirm_time, cycle.entry_confirm_time,
              cycle.entry_price, cycle.exit_signal_time, cycle.exit_time, cycle.exit_price, cycle.exit_type, cycle.quantity,
              cycle.invested_amount, cycle.realized_profit, cycle.invested_return_rate, cycle.capital_return_rate,
              int((cycle.exit_time-cycle.entry_confirm_time).total_seconds()), cycle.data_status))
            cycle_id = cur.fetchone()[0]
            cur.execute("UPDATE research_trade_cycle SET gross_realized_profit=%s,buy_fee=%s,sell_fee=%s,sell_tax=%s,total_trading_cost=%s WHERE cycle_id=%s",
                        (cycle.gross_realized_profit, cycle.buy_fee, cycle.sell_fee, cycle.sell_tax, cycle.total_trading_cost, cycle_id))
            return cycle_id

    def save_leg(self, *, cycle_id: int, leg) -> None:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_trade_leg(cycle_id,signal_type,entry_time,entry_price,entry_ratio,quantity,invested_amount)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (cycle_id,signal_type) DO NOTHING""",
                        (cycle_id, leg.signal_type, leg.entry_time, leg.entry_price, leg.ratio, leg.quantity, leg.invested_amount))

    def save_open_cycle(self, *, run_id: UUID, trade_stock_code: str, signal_source_stock_code: str, cycle) -> int:
        """Persist a daily research position without fabricating a closing bar."""
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_trade_cycle(run_id,trading_date,trade_stock_code,signal_source_stock_code,exit_signal_source_stock_code,strategy_code,observation_code,direction,entry_signal_time,entry_confirm_time,entry_time,entry_price,quantity,invested_amount,data_status)
              VALUES (%s,%s,%s,%s,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s,'OPEN')
              ON CONFLICT (run_id,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,entry_time)
              DO UPDATE SET data_status='OPEN' RETURNING cycle_id""", (run_id,cycle.entry_confirm_time.date(),trade_stock_code,signal_source_stock_code,signal_source_stock_code,cycle.strategy_code,cycle.direction,cycle.entry_signal_time,cycle.entry_confirm_time,cycle.entry_confirm_time,cycle.entry_price,cycle.quantity,cycle.invested_amount))
            return cur.fetchone()[0]

    def rebuild_performance(self, *, run_id: UUID, start_date: date, end_date: date) -> None:
        """Replace deterministic aggregate rows, never increment them."""
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("DELETE FROM research_performance_daily WHERE run_id=%s", (run_id,))
            cur.execute("""INSERT INTO research_performance_daily
            (run_id,trading_date,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction,session_code,closed_count,win_count,loss_count,flat_count,realized_profit,invested_amount,invested_return_rate,capital_return_rate,avg_trade_return_rate,avg_holding_seconds,signal_exit_profit,session_close_profit)
            SELECT run_id,trading_date,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction,'ALL',
              count(*),count(*) FILTER (WHERE realized_profit>0),count(*) FILTER (WHERE realized_profit<0),count(*) FILTER (WHERE realized_profit=0),
              coalesce(sum(realized_profit),0),coalesce(sum(invested_amount),0),
              coalesce(sum(realized_profit)/nullif(sum(invested_amount),0)*100,0),coalesce(sum(realized_profit)/10000000*100,0),
              coalesce(avg(invested_return_rate),0),coalesce(avg(holding_seconds),0),
              coalesce(sum(realized_profit) FILTER (WHERE exit_type='SIGNAL'),0),coalesce(sum(realized_profit) FILTER (WHERE exit_type='SESSION_CLOSE'),0)
            FROM research_trade_cycle WHERE run_id=%s AND exit_time IS NOT NULL
            GROUP BY run_id,trading_date,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction""", (run_id,))
            # A daily market label is research traceability, not a strategy
            # input.  Use the target instrument's first/last official bar of
            # the same trading day; absent RAW deliberately remains NULL.
            cur.execute("""WITH targets AS MATERIALIZED (
                SELECT DISTINCT trading_date,trade_stock_code
                  FROM research_performance_daily WHERE run_id=%s
              ), ranked_bars AS (
                SELECT t.trading_date,t.trade_stock_code,b.open_price,b.close_price,
                       row_number() OVER (PARTITION BY t.trading_date,t.trade_stock_code ORDER BY b.bar_time) AS first_rank,
                       row_number() OVER (PARTITION BY t.trading_date,t.trade_stock_code ORDER BY b.bar_time DESC) AS last_rank
                  FROM targets t
                  JOIN raw_stock_minute b
                    ON b.stock_code=t.trade_stock_code
                   AND b.bar_time >= t.trading_date::timestamp
                   AND b.bar_time < (t.trading_date + INTERVAL '1 day')::timestamp
                   AND b.market_code='KOSPI' AND b.data_source='KIS' AND b.collect_cycle='1MIN'
                   AND b.trading_venue=CASE WHEN t.trade_stock_code IN ('000660','005930') THEN 'INTEGRATED' ELSE 'KRX' END
              ), day_price AS (
                SELECT trading_date,trade_stock_code,
                       max(open_price) FILTER (WHERE first_rank=1) AS opening_price,
                       max(close_price) FILTER (WHERE last_rank=1) AS closing_price
                  FROM ranked_bars
                 GROUP BY trading_date,trade_stock_code
              )
              UPDATE research_performance_daily p
                 SET daily_return_rate=(d.closing_price-d.opening_price)/NULLIF(d.opening_price,0)*100,
                     daily_market_direction=CASE WHEN d.closing_price>d.opening_price THEN 'UP'
                                                  WHEN d.closing_price<d.opening_price THEN 'DOWN' ELSE 'FLAT' END
                FROM day_price d
               WHERE p.run_id=%s AND p.trading_date=d.trading_date AND p.trade_stock_code=d.trade_stock_code
                 AND d.opening_price IS NOT NULL AND d.closing_price IS NOT NULL""", (run_id, run_id))

    def top_period(self, *, run_id: UUID, limit: int = 10):
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("""SELECT run_id,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction,
              SUM(closed_count),SUM(win_count),SUM(loss_count),SUM(flat_count),SUM(realized_profit),SUM(invested_amount),
              COALESCE(SUM(realized_profit)/NULLIF(SUM(invested_amount),0)*100,0),COALESCE(SUM(realized_profit)/10000000*100,0),
              COALESCE(SUM(avg_trade_return_rate*closed_count)/NULLIF(SUM(closed_count),0),0),
              COALESCE(SUM(avg_holding_seconds*closed_count)/NULLIF(SUM(closed_count),0),0),SUM(signal_exit_profit),SUM(session_close_profit)
              FROM research_performance_daily WHERE run_id=%s GROUP BY run_id,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction
              ORDER BY COALESCE(SUM(realized_profit)/10000000*100,0) DESC LIMIT %s""", (run_id,limit))
            return cur.fetchall()

    def save_position_daily(self, *, run_id: UUID, cycle_id: int, trading_date: date,
                            trade_stock_code: str, signal_source_stock_code: str,
                            strategy_code: str, observation_code: str, direction: str,
                            entry_date: date, entry_price, valuation_close_price, quantity: int,
                            invested_amount, unrealized_profit, unrealized_return_rate,
                            capital_return_rate, position_status: str) -> None:
        """Persistence hook for a future daily strategy; it never changes intraday performance."""
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_position_daily
              (run_id,cycle_id,trading_date,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction,entry_date,entry_price,valuation_close_price,quantity,invested_amount,unrealized_profit,unrealized_return_rate,capital_return_rate,position_status)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (run_id,cycle_id,trading_date) DO UPDATE SET valuation_close_price=EXCLUDED.valuation_close_price,unrealized_profit=EXCLUDED.unrealized_profit,unrealized_return_rate=EXCLUDED.unrealized_return_rate,capital_return_rate=EXCLUDED.capital_return_rate,position_status=EXCLUDED.position_status""",
                        (run_id,cycle_id,trading_date,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction,entry_date,entry_price,valuation_close_price,quantity,invested_amount,unrealized_profit,unrealized_return_rate,capital_return_rate,position_status))
