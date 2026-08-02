"""테스트 DB에서만 다중 MA 영속화의 자연키와 재실행 안전성을 확인한다."""
from datetime import date, datetime
from decimal import Decimal
import os
import unittest

from src.repository.database import DatabaseSettings, create_connection_pool
from src.repository.multi_ma_performance_repository import MultiMaPerformanceKey, MultiMaPerformanceRepository


@unittest.skipUnless(os.getenv("DB_INTEGRATION_TEST") == "1" and "test" in os.getenv("DB_NAME", "").lower(), "DB_INTEGRATION_TEST=1인 테스트 DB에서만 실행")
class MultiMaPerformanceRepositoryIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool=create_connection_pool(DatabaseSettings.from_environment())
        cls.repo=MultiMaPerformanceRepository(cls.pool)
        cls.key=MultiMaPerformanceKey(date(2099,1,2),'TEST_MULTI_MA','INTEGRATED','ACCUMULATED','SEC_05','MA_3_5_10','CLOSE')
        cls.when=datetime(2099,1,2,9,0)

    @classmethod
    def tearDownClass(cls):
        cls.pool.close()

    def test_signal_cycle_legs_session_close_and_summary_are_idempotent(self):
        self.repo.save_signal(self.key,signal_time=self.when,signal_no='SIGNAL_1',direction='LONG',price=Decimal('100'),reason='integration')
        self.assertFalse(self.repo.save_signal(self.key,signal_time=self.when,signal_no='SIGNAL_1',direction='LONG',price=Decimal('100'),reason='integration'))
        trade_id,cycle_no=self.repo.create_trade(self.key,direction='LONG',entry_time=self.when,entry_price=Decimal('100'),entry_ratio=Decimal('0.333333'),average_entry_price=Decimal('100'))
        self.assertGreaterEqual(cycle_no,1)
        self.assertTrue(self.repo.add_trade_leg(trade_id=trade_id,signal_no='SIGNAL_1',signal_time=self.when,entry_price=Decimal('100'),entry_ratio=Decimal('0.333333'),notional_amount=Decimal('333333')))
        self.assertFalse(self.repo.add_trade_leg(trade_id=trade_id,signal_no='SIGNAL_1',signal_time=self.when,entry_price=Decimal('100'),entry_ratio=Decimal('0.333333'),notional_amount=Decimal('333333')))
        self.assertTrue(self.repo.close_trade(trade_id=trade_id,exit_time=self.when.replace(hour=15,minute=30),exit_price=Decimal('110'),exit_type='SESSION_CLOSE',exit_reason='SESSION_END',profit=Decimal('33.3333'),profit_rate=Decimal('0.003333')))
        self.assertFalse(self.repo.close_trade(trade_id=trade_id,exit_time=self.when.replace(hour=15,minute=30),exit_price=Decimal('110'),exit_type='SESSION_CLOSE',exit_reason='SESSION_END',profit=Decimal('33.3333'),profit_rate=Decimal('0.003333')))
        self.repo.rebuild_daily_summary(self.key,initial_capital=Decimal('1000000'))
        self.assertIsNone(self.repo.get_open_trade(self.key))
