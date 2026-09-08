import unittest
from pathlib import Path
from src.flow_v3.accounting import quantity
from src.service.flow_v3_dashboard_service import LIVE_CANDIDATES

ROOT=Path(__file__).resolve().parents[1]


class PreparationTest(unittest.TestCase):
    def test_whitelist_unique(self):
        self.assertEqual(len(LIVE_CANDIDATES),15)
        self.assertEqual(len(set(LIVE_CANDIDATES)),15)
        self.assertEqual(len(LIVE_CANDIDATES[:11]),11)

    def test_variable_share_not_fixed_one(self):
        self.assertEqual([quantity(x,100) for x in (150,200,350)],[1,2,3])

    def test_non_submittable_schema(self):
        sql=(ROOT/'database/migrations/20260909_flow_v3_live_preparation.sql').read_text(encoding='utf-8')
        self.assertIn('CHECK(NOT send_enabled)',sql)
        self.assertIn("CHECK(preparation_status='NO_SEND_NOT_SUBMITTABLE')",sql)
        self.assertIn('UNIQUE(strategy_id,entry_event_key)',sql)

    def test_no_broker_path_or_historical_replay(self):
        code=(ROOT/'src/flow_v3/live_preparation.py').read_text(encoding='utf-8')
        self.assertNotIn('live_order_request',code)
        self.assertNotIn('requests.post',code)
        self.assertIn('e.entry_signal_time >=',code)
        self.assertIn('i.strategy_id=e.strategy_id',code)


if __name__=='__main__':unittest.main()
