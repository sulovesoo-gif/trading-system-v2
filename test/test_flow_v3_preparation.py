import unittest
from pathlib import Path
from src.flow_v3.accounting import quantity

ROOT=Path(__file__).resolve().parents[1]


class PreparationTest(unittest.TestCase):
    def test_dashboard_candidates_follow_current_operations(self):
        code=(ROOT/'src/service/flow_v3_dashboard_service.py').read_text(encoding='utf-8')
        self.assertNotIn('LIVE_CANDIDATES',code)
        self.assertIn("op.operation_status='LIVE' AND op.effective_to IS NULL",code)

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
