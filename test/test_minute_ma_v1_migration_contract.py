import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class MinuteMaV1MigrationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=(ROOT/'database/migrations/20260828_minute_ma_v10_policy_additive.sql').read_text(encoding='utf-8')

    def test_additive_policy_identity(self):
        for token in ('minute_ma_operation_policy','minute_ma_policy_path','MINUTE_MA_V1_SHORT',
                      'MINUTE_MA_V1_LONG','HOLD_TO_NORMAL_EXIT_OR_STOP'):
            self.assertIn(token,self.sql)
        self.assertNotIn('DELETE FROM minute_ma_path',self.sql)
        self.assertNotIn('UPDATE minute_ma_path',self.sql)

    def test_stop_and_scope_contracts(self):
        for token in ('underlying_entry_reference_price','stop_threshold_price','STOP_EXIT',
                      'vw_minute_ma_current_selection_scoped','selection_scope'):
            self.assertIn(token,self.sql)

    def test_candidates_are_hold_only(self):
        self.assertIn("approval_status VARCHAR(16) NOT NULL DEFAULT 'HOLD'",self.sql)
        self.assertNotIn("INSERT INTO minute_ma_operation(",self.sql)
        self.assertNotIn("INSERT INTO minute_ma_compound_capital(",self.sql)

    def test_systemd_actual_send_default_off(self):
        unit=(ROOT/'systemd/trading-minute-ma-live.service').read_text(encoding='utf-8')
        self.assertIn('MINUTE_MA_ACTUAL_SEND=N',unit)
        self.assertNotIn('MINUTE_MA_ACTUAL_SEND=Y',unit)

    def test_policy_operation_capital_and_selection_are_independent(self):
        for token in ('minute_ma_policy_operation','minute_ma_policy_compound_capital',
                      'vw_minute_ma_v1_current_selection','minute_policy_operation_id'):
            self.assertIn(token,self.sql)
        apply=(ROOT/'database/migrations/20260828_minute_ma_v10_prod_apply.sql').read_text(encoding='utf-8')
        self.assertIn("v1_live<>20",apply);self.assertIn("capital_sum<>3200000",apply)
        self.assertNotIn("UPDATE minute_ma_path",apply)

    def test_v1_paper_writes_but_cannot_enable_broker_send(self):
        unit=(ROOT/'systemd/trading-minute-ma-v1-paper.service').read_text(encoding='utf-8')
        self.assertIn('MINUTE_MA_V1_PAPER_WRITE=Y',unit)
        self.assertIn('--write',unit)
        self.assertNotIn('MINUTE_MA_ACTUAL_SEND',unit)

    def test_actual_entrypoint_is_v1_and_has_no_eod(self):
        source=(ROOT/'scripts/runtime/run_minute_ma_actual.py').read_text(encoding='utf-8')
        self.assertIn('MinuteMaV1LiveRuntime',source)
        self.assertIn('MinuteMaV1LiveNoSendRuntime',source)
        self.assertIn("'mode':'V1_LIVE_NOSEND'",source)
        for forbidden in ('plan_eod(', 'close_eod(', 'EOD_1519'):
            self.assertNotIn(forbidden,source)

    def test_rollback_guards_all_v1_durable_families(self):
        rollback=(ROOT/'database/migrations/20260828_minute_ma_v10_policy_guarded_rollback.sql').read_text(encoding='utf-8')
        for token in ('minute_ma_policy_operation','minute_ma_policy_compound_capital',
          'minute_ma_live_checkpoint_allocation','minute_ma_live_broker_cost_allocation',
          'execution_logical_position','MINUTE_MA_V1_OPERATION'):
            self.assertIn(token,rollback)

if __name__=='__main__':unittest.main()
