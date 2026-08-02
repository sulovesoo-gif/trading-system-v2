from datetime import date, datetime
from decimal import Decimal
import unittest

from src.repository.multi_ma_performance_repository import MultiMaPerformanceKey, OBSERVATION_CODES, STRATEGY_CODES
from src.service.multi_ma_performance_service import MultiMaPerformanceService


class FakeRepository:
    def __init__(self): self.signals=set(); self.trades=[]; self.legs=[]; self.closed=[]; self.rebuilt=[]
    def save_signal(self,key,**v):
        natural=(*key.values(),v['signal_time'],v['signal_no'],v['direction'])
        if natural in self.signals:return False
        self.signals.add(natural);return True
    def create_trade(self,key,**v):
        number=1+sum(t['key']==key for t in self.trades); trade_id=len(self.trades)+1; self.trades.append({'id':trade_id,'key':key,**v});return trade_id,number
    def add_trade_leg(self,**v):
        natural=(v['trade_id'],v['signal_no'])
        if natural in {(x['trade_id'],x['signal_no']) for x in self.legs}:return False
        self.legs.append(v);return True
    def close_trade(self,**v): self.closed.append(v);return True
    def rebuild_daily_summary(self,key,**v): self.rebuilt.append((key,v))


class PerformanceServiceTest(unittest.TestCase):
    def setUp(self):
        self.repo=FakeRepository(); self.service=MultiMaPerformanceService(self.repo,initial_capital=Decimal('900'))
        self.key=MultiMaPerformanceKey(date(2026,7,30),'000660','INTEGRATED','ACCUMULATED','SEC_05','MA_3_5_10','CLOSE')
        self.when=datetime(2026,7,30,9,0)

    def test_same_signal_replay_does_not_create_trade_or_leg_twice(self):
        self.assertTrue(self.service.process_signal(self.key,signal_no='SIGNAL_1',direction='LONG',signal_time=self.when,price=Decimal('100'),reason='test'))
        self.assertFalse(self.service.process_signal(self.key,signal_no='SIGNAL_1',direction='LONG',signal_time=self.when,price=Decimal('100'),reason='test'))
        self.assertEqual(len(self.repo.trades),1); self.assertEqual(len(self.repo.legs),1)

    def test_opposite_signal_closes_then_creates_next_cycle(self):
        self.service.process_signal(self.key,signal_no='SIGNAL_1',direction='LONG',signal_time=self.when,price=Decimal('100'),reason='open')
        self.service.process_signal(self.key,signal_no='SIGNAL_2',direction='SHORT',signal_time=self.when.replace(minute=1),price=Decimal('90'),reason='reverse')
        self.assertEqual(len(self.repo.closed),1); self.assertEqual(self.repo.closed[0]['exit_type'],'SIGNAL'); self.assertEqual(len(self.repo.trades),2)

    def test_session_close_is_idempotent_and_resets_to_flat(self):
        self.service.process_signal(self.key,signal_no='SIGNAL_1',direction='LONG',signal_time=self.when,price=Decimal('100'),reason='open')
        self.assertTrue(self.service.session_close(self.key,exit_time=self.when.replace(hour=15,minute=30),exit_price=Decimal('110')))
        self.assertFalse(self.service.session_close(self.key,exit_time=self.when.replace(hour=15,minute=30),exit_price=Decimal('110')))
        self.assertEqual(self.repo.closed[0]['exit_type'],'SESSION_CLOSE'); self.assertEqual(self.repo.closed[0]['exit_reason'],'SESSION_END')

    def test_all_48_settings_keep_independent_runtime_state(self):
        for strategy in STRATEGY_CODES:
            for observation in OBSERVATION_CODES:
                key=MultiMaPerformanceKey(date(2026,7,30),'000660','INTEGRATED',strategy,observation,'MA_3_5_10','CLOSE')
                self.service._state(key)
        self.assertEqual(len(self.service.runtime),48)
