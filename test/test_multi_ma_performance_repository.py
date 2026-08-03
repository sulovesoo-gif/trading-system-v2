from datetime import date
from decimal import Decimal
import unittest

from src.analysis.strategy.multi_ma_performance import Portfolio
from src.repository.multi_ma_performance_repository import (
    MultiMaPerformanceKey,
    MultiMaPerformanceRepository,
    OBSERVATION_CODES,
    STRATEGY_CODES,
)


class _Cursor:
    def __init__(self):
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, params):
        self.params = params

    def fetchone(self):
        return (1,)


class _Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def transaction(self):
        return self

    def cursor(self):
        return self.cursor_value


class _Pool:
    def __init__(self, cursor):
        self.cursor_value = cursor

    def connection(self):
        return _Connection(self.cursor_value)


class MultiMaPerformanceModelTest(unittest.TestCase):
    def key(self, strategy="ACCUMULATED", obs="SEC_05", price="CLOSE"):
        return MultiMaPerformanceKey(date(2026, 7, 30), "000660", "INTEGRATED", strategy, obs, "MA_3_5_10", price)

    def test_key_accepts_exact_12_observation_codes(self):
        self.assertEqual(len(OBSERVATION_CODES), 12)
        self.assertEqual(len(STRATEGY_CODES) * len(OBSERVATION_CODES), 48)
        self.key().values()

    def test_invalid_observation_code_is_rejected(self):
        with self.assertRaises(ValueError):
            self.key(obs="5").values()

    def test_weighted_leg_profit_and_session_close(self):
        portfolio = Portfolio(Decimal("900"))
        portfolio.enter("LONG", Decimal("100"), Decimal("0.333333333333"), "SIGNAL_1")
        portfolio.enter("LONG", Decimal("110"), Decimal("0.333333333333"), "SIGNAL_2")
        profit, _ = portfolio.close(Decimal("120"))
        self.assertGreater(profit, 0)
        self.assertEqual(portfolio.direction, "FLAT")

    def test_save_signal_keeps_observation_and_config_axes_in_their_columns(self):
        cursor = _Cursor()
        repository = MultiMaPerformanceRepository(_Pool(cursor))
        key = self.key(strategy="SIGNAL_2", obs="SEC_10", price="CLOSE")
        saved = repository.save_signal(
            key,
            signal_time="2026-07-30 09:01:10",
            signal_no="SIGNAL_2",
            direction="SHORT",
            price=Decimal("100"),
            reason="test",
        )
        self.assertTrue(saved)
        # SQL placeholders omit the literal market_code='KOSPI'.
        self.assertEqual(cursor.params[:9], (
            key.trade_date,
            "000660",
            "INTEGRATED",
            "SIGNAL_2",
            "SEC_10",
            "SEC_10",
            "MA_3_5_10",
            "CLOSE",
            "2026-07-30 09:01:10",
        ))
