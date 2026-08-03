"""다중 MA 분석 결과의 최소 저장 계층."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class MultiMaStateKey:
    stock_code: str
    market_code: str
    trading_venue: str
    strategy_code: str
    analysis_slot: str
    ma_config_code: str
    price_field_code: str


class MultiMaRepository:
    def __init__(self, pool) -> None:
        self.pool = pool

    def upsert_state(self, key: MultiMaStateKey, *, last_processed_time, ma_short, ma_mid, ma_long, short_slope, previous_short_slope, direction, weight, applied_signals) -> None:
        sql = (
            "INSERT INTO analysis_multi_ma_state (stock_code, market_code, trading_venue, strategy_code, analysis_slot, ma_config_code, price_field_code, "
            "last_processed_time, ma_short, ma_mid, ma_long, short_slope, previous_short_slope, position_direction, position_weight, applied_signals) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (stock_code, market_code, trading_venue, strategy_code, analysis_slot, ma_config_code, price_field_code) DO UPDATE SET "
            "last_processed_time=EXCLUDED.last_processed_time, ma_short=EXCLUDED.ma_short, ma_mid=EXCLUDED.ma_mid, ma_long=EXCLUDED.ma_long, "
            "short_slope=EXCLUDED.short_slope, previous_short_slope=EXCLUDED.previous_short_slope, position_direction=EXCLUDED.position_direction, "
            "position_weight=EXCLUDED.position_weight, applied_signals=EXCLUDED.applied_signals, updated_at=CURRENT_TIMESTAMP"
        )
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(sql, (*key.__dict__.values(), last_processed_time, ma_short, ma_mid, ma_long, short_slope, previous_short_slope, direction, weight, Jsonb(list(sorted(applied_signals)))))

    def record_signal(self, key: MultiMaStateKey, *, signal, feature) -> bool:
        sql = (
            "INSERT INTO analysis_multi_ma_signal (stock_code, market_code, trading_venue, strategy_code, analysis_slot, ma_config_code, price_field_code, "
            "signal_type, direction, signal_time, signal_price, ma_short, ma_mid, ma_long, short_slope, reason) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING signal_id"
        )
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(sql, (*key.__dict__.values(), signal.signal_type, signal.direction, feature.bar.bar_time, feature.value,
                                      feature.ma_short, feature.ma_mid, feature.ma_long, feature.short_slope, signal.reason))
                    return cur.fetchone() is not None

    def load_feature_state(self, key: MultiMaStateKey):
        """Return the last feature for one observation slot after a restart."""
        sql = (
            "SELECT last_processed_time, ma_short, ma_mid, ma_long, short_slope "
            "FROM analysis_multi_ma_state WHERE stock_code=%s AND market_code=%s "
            "AND trading_venue=%s AND strategy_code=%s AND analysis_slot=%s "
            "AND ma_config_code=%s AND price_field_code=%s"
        )
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(key.__dict__.values()))
                return cur.fetchone()

    @staticmethod
    def new_cycle_id():
        return uuid4()
