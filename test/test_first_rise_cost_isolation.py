import json
import unittest
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from src.collector.raw.kis_client import KISClientError
from src.first_rise_breakout.j_live_runtime import JLiveRuntime


class CostIsolationTests(unittest.TestCase):
    def runtime(self,error,opened=True):
        self.normal={'finalized':2}
        self.cost=Mock();self.cost.finalize_due.side_effect=[error,self.normal]
        self.planner=Mock();self.planner.entry_signals.return_value=['signal']
        self.planner.plan_buy.return_value='READY';self.planner.plan_exits.return_value=1
        self.recovery=Mock();self.cancel=Mock();self.capacity=Mock()
        self.store=Mock();self.store.discover_ready_request_keys.side_effect=[['exit'],['buy'],[],[]]
        self.submitter=Mock();self.submitter.process_request.return_value=(None,'ACK')
        return JLiveRuntime(context=SimpleNamespace(load=Mock(),config=object()),
            planner=self.planner,submit_store=self.store,submitter=self.submitter,
            recovery=self.recovery,price_lookup=Mock(),cash_lookup=Mock(),
            cost_finalizer=self.cost,cancellations=self.cancel,capacity_monitor=self.capacity,
            trading_day=lambda _:opened)

    def test_cost_exceptions_isolated_and_next_cycle_recovers(self):
        at=datetime(2026,10,6,9,30)
        for error in (KISClientError('mock HTTP 500'),Exception('mock unexpected')):
            with self.subTest(error=type(error).__name__):
                runtime=self.runtime(error)
                with self.assertLogs('src.first_rise_breakout.j_live_runtime',level='ERROR') as logs:
                    result=runtime.cycle(at=at)
                self.assertEqual(result['costs'],{'status':'DEFERRED','error_type':type(error).__name__})
                json.dumps(result['costs'])
                self.assertIn('FIRST_RISE_COST_FINALIZATION_ERROR',logs.output[0])
                self.assertIn('Traceback',logs.output[0])
                self.assertEqual(self.recovery.poll.call_count,2)
                self.cancel.cycle.assert_called_once_with(at=at)
                self.planner.plan_exits.assert_called_once_with(at=at)
                self.planner.plan_buy.assert_called_once()
                self.assertEqual(self.submitter.process_request.call_count,2)
                self.capacity.refresh.assert_called_once_with(at=at)
                second=runtime.cycle(at=at+timedelta(seconds=60))
                self.assertIs(second['costs'],self.normal)
                self.assertEqual(self.cost.finalize_due.call_count,2)
                self.assertEqual(self.capacity.refresh.call_count,2)

    def test_holiday_still_blocks_buy_without_blocking_recovery_or_monitor(self):
        runtime=self.runtime(KISClientError('mock'),opened=False)
        with self.assertLogs('src.first_rise_breakout.j_live_runtime',level='ERROR'):
            result=runtime.cycle(at=datetime(2026,10,5,9,30))
        self.assertEqual(result['planning'],[])
        self.planner.plan_buy.assert_not_called()
        self.assertEqual(self.recovery.poll.call_count,2)
        self.capacity.refresh.assert_called_once()
