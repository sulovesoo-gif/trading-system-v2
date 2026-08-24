import unittest
from datetime import date

from scripts.realtime.run_stock_daily_collector import collect_and_log
from src.service.stock_daily_collection_service import StockDailyCollectionItem

class StockDailyRuntimeRunnerTest(unittest.TestCase):
 def test_collect_once_preserves_each_target_failure_evidence(self):
  class Runner:
   def collect_trade_date(self, trading_date):
    self.date=trading_date
    return [StockDailyCollectionItem('005930','KRX','OK',1,0),StockDailyCollectionItem('000660','KRX','FAILED',error='KISClientError')]
  runner=Runner();self.assertEqual(collect_and_log(runner,date(2026,8,24)),1);self.assertEqual(runner.date,date(2026,8,24))
