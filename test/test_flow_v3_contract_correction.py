import unittest
from datetime import datetime
from decimal import Decimal
from src.flow_v3.contract_correction import corrected_trades
from src.flow_v3.engine import FlowV3SignalEngine
from test.test_flow_v3_runtime import strategy, state


class CorrectionTest(unittest.TestCase):
    def test_entry_cutoff_for_both_policies(self):
        engine=FlowV3SignalEngine()
        for policy in ('SIGNAL_EOD','SIGNAL_HOLD'):
            contracts=[strategy('F1',policy=policy)]
            before=state(datetime(2026,9,4,15,18),flow_cross=1)
            after=state(datetime(2026,9,4,15,19),flow_cross=1)
            self.assertEqual(len(engine.entry_signals(state=before,recent_states=[],strategies=contracts)),1)
            self.assertEqual(engine.entry_signals(state=after,recent_states=[],strategies=contracts),())

    def test_exclusion_preserves_original(self):
        row = dict(paper_trade_id=678036,entry_signal_time=datetime(2026,9,4,15,19),trade_status='CLOSED')
        self.assertEqual(corrected_trades([row],{678036:dict(excluded_from_corrected_performance=True)}),[])
        self.assertEqual(row['trade_status'],'CLOSED')

    def test_exclusion_does_not_remove_valid_entry(self):
        row = dict(paper_trade_id=1,entry_signal_time=datetime(2026,9,4,15,18))
        with self.assertRaisesRegex(ValueError,'EXCLUSION_BEFORE_CUTOFF'):
            corrected_trades([row],{1:dict(excluded_from_corrected_performance=True)})

    def test_overlay_is_non_mutating_and_equal_time_valid(self):
        at=datetime(2026,9,4,15,19)
        row=dict(paper_trade_id=1,entry_execution_time=at,actual_exit_time=datetime(2026,9,4,15,29))
        correction=dict(excluded_from_corrected_performance=False,corrected_exit_time=at,corrected_exit_price=Decimal('7320'))
        new=corrected_trades([row],{1:correction})[0]
        self.assertEqual(new['actual_exit_time'],at)
        self.assertEqual(row['actual_exit_time'].minute,29)

    def test_reversed_overlay_blocked(self):
        row=dict(paper_trade_id=1,entry_execution_time=datetime(2026,9,4,15,20))
        correction=dict(excluded_from_corrected_performance=False,corrected_exit_time=datetime(2026,9,4,15,19),corrected_exit_price=1)
        with self.assertRaisesRegex(ValueError,'CORRECTED_EXIT_BEFORE_ENTRY'):
            corrected_trades([row],{1:correction})


if __name__=='__main__': unittest.main()
