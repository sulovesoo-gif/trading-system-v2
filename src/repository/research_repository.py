"""Persistence for reproducible research runs only; never a live trading repository."""
from __future__ import annotations

from datetime import date
from uuid import UUID


class ResearchRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def create_run(self, *, run_id: UUID, start_date: date, end_date: date, parameters: dict, status: str = "RUNNING") -> None:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_run(run_id,start_date,end_date,status,parameters)
            VALUES (%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING""", (run_id, start_date, end_date, status, parameters))

    def finish_run(self, run_id: UUID, status: str) -> None:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("UPDATE research_run SET status=%s WHERE run_id=%s", (status, run_id))

    def save_feature(self, *, run_id: UUID, stock_code: str, feature, ma10_direction: str | None, data_status: str = "NORMAL") -> None:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_feature(run_id,trading_date,stock_code,observation_code,observation_time,price,ma3,ma5,ma10,ma10_direction,data_status)
            VALUES (%s,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id,stock_code,observation_code,observation_time) DO NOTHING""",
                        (run_id, feature.bar.bar_time.date(), stock_code, feature.bar.bar_time, feature.value,
                         feature.ma_short, feature.ma_mid, feature.ma_long, ma10_direction, data_status))

    def save_signal(self, *, run_id: UUID, stock_code: str, strategy_code: str, signal, pending: bool, confirm_time, session_code: str | None, data_status: str = "NORMAL") -> None:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_signal_event(run_id,trading_date,signal_source_stock_code,observation_code,signal_time,strategy_code,signal_type,direction,signal_price,ma3,ma5,ma10,ma10_direction,pending_yn,pending_started_at,confirm_time,session_code,data_status)
            VALUES (%s,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id,signal_source_stock_code,observation_code,signal_time,strategy_code,signal_type,direction) DO NOTHING""",
                        (run_id, signal.at.date(), stock_code, signal.at, strategy_code, signal.signal_type, signal.direction,
                         signal.feature.value, signal.feature.ma_short, signal.feature.ma_mid, signal.feature.ma_long, None,
                         "Y" if pending else "N", signal.at if pending else None, confirm_time, session_code, data_status))

    def save_cycle(self, *, run_id: UUID, trade_stock_code: str, signal_source_stock_code: str, cycle) -> int:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_trade_cycle(run_id,trading_date,trade_stock_code,signal_source_stock_code,exit_signal_source_stock_code,strategy_code,observation_code,direction,entry_signal_time,entry_confirm_time,entry_time,entry_price,exit_signal_time,exit_time,exit_price,exit_type,quantity,invested_amount,realized_profit,invested_return_rate,capital_return_rate,holding_seconds,data_status)
            VALUES (%s,%s,%s,%s,%s,%s,'COMPLETE',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,entry_time)
            DO UPDATE SET exit_time=EXCLUDED.exit_time,exit_price=EXCLUDED.exit_price,exit_type=EXCLUDED.exit_type,realized_profit=EXCLUDED.realized_profit
            RETURNING cycle_id""", (run_id, cycle.entry_confirm_time.date(), trade_stock_code, signal_source_stock_code, signal_source_stock_code,
              cycle.strategy_code, cycle.direction, cycle.entry_signal_time, cycle.entry_confirm_time, cycle.entry_confirm_time,
              cycle.entry_price, cycle.exit_signal_time, cycle.exit_time, cycle.exit_price, cycle.exit_type, cycle.quantity,
              cycle.invested_amount, cycle.realized_profit, cycle.invested_return_rate, cycle.capital_return_rate,
              int((cycle.exit_time-cycle.entry_confirm_time).total_seconds()), cycle.data_status))
            return cur.fetchone()[0]

    def save_leg(self, *, cycle_id: int, leg) -> None:
        with self.pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("""INSERT INTO research_trade_leg(cycle_id,signal_type,entry_time,entry_price,entry_ratio,quantity,invested_amount)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (cycle_id,signal_type) DO NOTHING""",
                        (cycle_id, leg.signal_type, leg.entry_time, leg.entry_price, leg.ratio, leg.quantity, leg.invested_amount))

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
            cur.execute("DELETE FROM research_performance_period WHERE run_id=%s AND start_date=%s AND end_date=%s", (run_id,start_date,end_date))
            cur.execute("""INSERT INTO research_performance_period
            SELECT run_id,%s,%s,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction,
              sum(closed_count),sum(win_count),sum(loss_count),sum(flat_count),sum(realized_profit),sum(invested_amount),
              coalesce(sum(realized_profit)/nullif(sum(invested_amount),0)*100,0),coalesce(sum(realized_profit)/10000000*100,0),
              avg(avg_trade_return_rate),avg(avg_holding_seconds),sum(signal_exit_profit),sum(session_close_profit)
            FROM research_performance_daily WHERE run_id=%s GROUP BY run_id,trade_stock_code,signal_source_stock_code,strategy_code,observation_code,direction""", (start_date,end_date,run_id))

    def top_period(self, *, run_id: UUID, limit: int = 10):
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM research_performance_period WHERE run_id=%s ORDER BY capital_return_rate DESC LIMIT %s", (run_id,limit))
            return cur.fetchall()
