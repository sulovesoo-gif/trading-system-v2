"""다중 MA 성과 저장소.

수집 실행기와 분리되어 테스트 DB의 분석 결과만 영속화한다. 주문 API는 사용하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from psycopg.types.json import Jsonb


OBSERVATION_CODES = ("SEC_05", "SEC_10", "SEC_15", "SEC_20", "SEC_25", "SEC_30", "SEC_35", "SEC_40", "SEC_45", "SEC_50", "SEC_55", "COMPLETE")
STRATEGY_CODES = ("SIGNAL_1", "SIGNAL_2", "SIGNAL_3", "ACCUMULATED")


@dataclass(frozen=True)
class MultiMaPerformanceKey:
    trade_date: object
    stock_code: str
    trading_venue: str
    strategy_code: str
    observation_code: str
    ma_config_code: str
    price_field_code: str

    def values(self) -> tuple:
        if self.observation_code not in OBSERVATION_CODES:
            raise ValueError(f"허용되지 않은 observation_code: {self.observation_code}")
        return tuple(self.__dict__.values())


class MultiMaPerformanceRepository:
    """신호 재생을 안전하게 여러 번 실행할 수 있는 PostgreSQL 저장소."""
    def __init__(self, pool) -> None:
        self.pool = pool

    def save_state(self, key: MultiMaPerformanceKey, *, last_processed_time, direction: str, weight: Decimal, applied_signals: Iterable[str]) -> None:
        """48개 조합의 재시작 가능한 분석 상태를 설정 축별로 저장한다."""
        sql = """INSERT INTO analysis_multi_ma_state
        (stock_code,market_code,trading_venue,strategy_code,observation_code,analysis_slot,ma_config_code,price_field_code,trade_date,last_processed_time,position_direction,position_weight,applied_signals)
        VALUES (%s,'KOSPI',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (stock_code,market_code,trading_venue,strategy_code,analysis_slot,ma_config_code,price_field_code) DO UPDATE SET
        trade_date=EXCLUDED.trade_date,last_processed_time=EXCLUDED.last_processed_time,position_direction=EXCLUDED.position_direction,
        position_weight=EXCLUDED.position_weight,applied_signals=EXCLUDED.applied_signals,updated_at=CURRENT_TIMESTAMP"""
        with self.pool.connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql, (key.stock_code,key.trading_venue,key.strategy_code,key.observation_code,key.observation_code,key.ma_config_code,key.price_field_code,
                                  key.trade_date,last_processed_time,direction,weight,Jsonb(list(sorted(applied_signals)))))

    def get_state(self, key: MultiMaPerformanceKey):
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT trade_date,last_processed_time,position_direction,position_weight,applied_signals
                FROM analysis_multi_ma_state WHERE stock_code=%s AND market_code='KOSPI' AND trading_venue=%s AND strategy_code=%s
                AND analysis_slot=%s AND ma_config_code=%s AND price_field_code=%s""",
                            (key.stock_code,key.trading_venue,key.strategy_code,key.observation_code,key.ma_config_code,key.price_field_code))
                return cur.fetchone()

    def save_signal(self, key: MultiMaPerformanceKey, *, signal_time, signal_no: str, direction: str, price: Decimal, reason: str) -> bool:
        sql = """INSERT INTO analysis_multi_ma_signal
        (trade_date,stock_code,market_code,trading_venue,strategy_code,observation_code,analysis_slot,ma_config_code,price_field_code,signal_time,signal_no,signal_type,direction,signal_price,reason)
        VALUES (%s,%s,'KOSPI',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING RETURNING signal_id"""
        with self.pool.connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql, (
                    key.trade_date, key.stock_code, key.trading_venue,
                    key.strategy_code, key.observation_code,
                    key.observation_code, key.ma_config_code,
                    key.price_field_code, signal_time,
                    signal_no, signal_no, direction, price, reason,
                ))
                return cur.fetchone() is not None

    def get_open_trade(self, key: MultiMaPerformanceKey):
        sql = """SELECT trade_id,cycle_no,direction,entry_time,entry_price,entry_ratio,average_entry_price
        FROM analysis_multi_ma_trade WHERE trade_date=%s AND stock_code=%s AND trading_venue=%s AND strategy_code=%s
        AND observation_code=%s AND ma_config_code=%s AND price_field_code=%s AND status='OPEN' FOR UPDATE"""
        with self.pool.connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql, key.values())
                return cur.fetchone()

    def get_trade_legs(self, trade_id: int):
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT signal_no,entry_price,entry_ratio FROM analysis_multi_ma_trade_leg WHERE trade_id=%s ORDER BY signal_time", (trade_id,))
                return cur.fetchall()

    def signal_is_applied(self, key: MultiMaPerformanceKey, *, signal_time, signal_no: str, direction: str) -> bool:
        """Distinguish an idempotent replay from a prior partial write."""
        sql = """SELECT 1 FROM analysis_multi_ma_trade_leg leg
        JOIN analysis_multi_ma_trade trade ON trade.trade_id=leg.trade_id
        WHERE trade_date=%s AND stock_code=%s AND trading_venue=%s AND strategy_code=%s
          AND observation_code=%s AND ma_config_code=%s AND price_field_code=%s
          AND leg.signal_time=%s AND leg.signal_no=%s AND trade.direction=%s LIMIT 1"""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (*key.values(), signal_time, signal_no, direction))
                return cur.fetchone() is not None

    def create_trade(self, key: MultiMaPerformanceKey, *, direction: str, entry_time, entry_price: Decimal, entry_ratio: Decimal, average_entry_price: Decimal):
        """설정 축 advisory lock 안에서 다음 cycle_no를 배정한다."""
        lock_key = "|".join(map(str, key.values()))
        with self.pool.connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
                cur.execute("""SELECT COALESCE(MAX(cycle_no),0)+1 FROM analysis_multi_ma_trade
                WHERE trade_date=%s AND stock_code=%s AND trading_venue=%s AND strategy_code=%s
                AND observation_code=%s AND ma_config_code=%s AND price_field_code=%s""", key.values())
                cycle_no = cur.fetchone()[0]
                cur.execute("""INSERT INTO analysis_multi_ma_trade
                (trade_date,stock_code,trading_venue,strategy_code,observation_code,ma_config_code,price_field_code,cycle_no,direction,entry_time,entry_price,entry_ratio,average_entry_price,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN') RETURNING trade_id,cycle_no""",
                            (*key.values(), cycle_no, direction, entry_time, entry_price, entry_ratio, average_entry_price))
                return cur.fetchone()

    def add_trade_leg(self, *, trade_id: int, signal_no: str, signal_time, entry_price: Decimal, entry_ratio: Decimal, notional_amount: Decimal) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("""INSERT INTO analysis_multi_ma_trade_leg
                (trade_id,signal_no,signal_time,entry_price,entry_ratio,notional_amount)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (trade_id,signal_no) DO NOTHING RETURNING trade_id""",
                            (trade_id, signal_no, signal_time, entry_price, entry_ratio, notional_amount))
                return cur.fetchone() is not None

    def close_trade(self, *, trade_id: int, exit_time, exit_price: Decimal, exit_type: str, exit_reason: str, profit: Decimal, profit_rate: Decimal) -> bool:
        if exit_type not in ("SIGNAL", "SESSION_CLOSE"):
            raise ValueError("exit_type은 SIGNAL 또는 SESSION_CLOSE여야 합니다.")
        with self.pool.connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("""UPDATE analysis_multi_ma_trade SET exit_time=%s,exit_price=%s,exit_type=%s,exit_reason=%s,
                realized_profit_amount=%s,realized_profit_rate=%s,status='CLOSED',updated_at=CURRENT_TIMESTAMP
                WHERE trade_id=%s AND status='OPEN' RETURNING trade_id""",
                            (exit_time, exit_price, exit_type, exit_reason, profit, profit_rate, trade_id))
                return cur.fetchone() is not None

    def rebuild_daily_summary(self, key: MultiMaPerformanceKey, *, initial_capital: Decimal):
        sql = """WITH closed AS (
          SELECT realized_profit_amount, exit_type FROM analysis_multi_ma_trade
          WHERE trade_date=%s AND stock_code=%s AND trading_venue=%s AND strategy_code=%s AND observation_code=%s
            AND ma_config_code=%s AND price_field_code=%s AND status='CLOSED'
        ), aggregate AS (
          SELECT COALESCE(SUM(realized_profit_amount),0) total_profit, COUNT(*) trade_count,
                 COUNT(*) FILTER (WHERE realized_profit_amount>0) win_count, COUNT(*) FILTER (WHERE realized_profit_amount<0) loss_count,
                 COUNT(*) FILTER (WHERE exit_type='SIGNAL') signal_count, COUNT(*) FILTER (WHERE exit_type='SESSION_CLOSE') close_count,
                 COALESCE(SUM(realized_profit_amount) FILTER (WHERE exit_type='SIGNAL'),0) signal_profit,
                 COALESCE(SUM(realized_profit_amount) FILTER (WHERE exit_type='SESSION_CLOSE'),0) close_profit,
                 MAX(realized_profit_amount) max_profit, MIN(realized_profit_amount) max_loss FROM closed)
        INSERT INTO analysis_multi_ma_summary
        (trade_date,stock_code,market_code,trading_venue,strategy_code,observation_code,analysis_slot,ma_config_code,price_field_code,initial_capital,total_profit_amount,total_profit_rate,trade_count,win_count,loss_count,win_rate,signal_exit_count,session_close_exit_count,signal_exit_profit,session_close_exit_profit,max_profit,max_loss,calculated_at)
        SELECT %s,%s,'KOSPI',%s,%s,%s,%s,%s,%s,%s,total_profit,total_profit/%s*100,trade_count,win_count,loss_count,
          CASE WHEN trade_count=0 THEN 0 ELSE win_count::numeric/trade_count*100 END,signal_count,close_count,signal_profit,close_profit,max_profit,max_loss,CURRENT_TIMESTAMP FROM aggregate
        """
        with self.pool.connection() as conn:
            with conn.transaction(), conn.cursor() as cur:
                # Older deployments retain a legacy primary key while newer
                # DDL adds an observation natural key.  Both identify the
                # same derived summary here.  Rebuild is deterministic, so
                # replacing that one row inside the transaction is safer
                # than selecting only one of two unique arbiters.
                cur.execute("""DELETE FROM analysis_multi_ma_summary
                WHERE trade_date=%s AND stock_code=%s AND trading_venue=%s AND strategy_code=%s
                  AND ma_config_code=%s AND price_field_code=%s
                  AND (observation_code=%s OR (market_code='KOSPI' AND analysis_slot=%s))""", (
                    key.trade_date, key.stock_code, key.trading_venue, key.strategy_code,
                    key.ma_config_code, key.price_field_code, key.observation_code, key.observation_code,
                ))
                cur.execute(sql, (
                    *key.values(), key.trade_date, key.stock_code, key.trading_venue,
                    key.strategy_code, key.observation_code, key.observation_code,
                    key.ma_config_code, key.price_field_code, initial_capital, initial_capital,
                ))

    def period_summary(self, *, start_date, end_date, stock_code: str | None = None):
        params: list[object] = [start_date, end_date]
        stock_filter = ""
        if stock_code:
            stock_filter = " AND stock_code=%s"; params.append(stock_code)
        sql = """SELECT strategy_code,observation_code,ma_config_code,price_field_code,SUM(total_profit_amount) total_profit,
        SUM(initial_capital) initial_capital,SUM(trade_count) trade_count,SUM(signal_exit_count) signal_exit_count,
        SUM(session_close_exit_count) session_close_exit_count,SUM(signal_exit_profit) signal_exit_profit,SUM(session_close_exit_profit) session_close_exit_profit
        FROM analysis_multi_ma_summary WHERE trade_date BETWEEN %s AND %s""" + stock_filter + " GROUP BY strategy_code,observation_code,ma_config_code,price_field_code ORDER BY SUM(total_profit_amount) DESC"
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params)); return cur.fetchall()

    def closed_equity_curve(self, *, key: MultiMaPerformanceKey):
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT exit_time,realized_profit_amount FROM analysis_multi_ma_trade WHERE trade_date=%s AND stock_code=%s
                AND trading_venue=%s AND strategy_code=%s AND observation_code=%s AND ma_config_code=%s AND price_field_code=%s AND status='CLOSED' ORDER BY exit_time,trade_id""", key.values())
                return cur.fetchall()
