import unittest
from pathlib import Path
class SelectionAuditContractTest(unittest.TestCase):
 def test_schema_keeps_snapshot_immutable_and_dashboard_canonical(self):
  text=Path('database/migrations/20260824_daily_strategy_selection_audit_additive.sql').read_text(encoding='utf-8')
  self.assertIn('daily_strategy_selection_batch',text);self.assertIn('daily_strategy_selection_snapshot',text)
  self.assertIn('snapshot is immutable',text);self.assertIn("m.strategy_role='CANONICAL'",text)
 def test_seed_has_full_universe_and_hard_guards(self):
  text=Path('scripts/db/seed_daily_ma_selection_20260824.py').read_text(encoding='utf-8')
  self.assertIn('len(strategies)!=2400 or len(selected)!=346',text)
  self.assertIn('(2400,346,2054,3,11,332,2054,3,0)',text)
  self.assertIn("status='APPROVED'",text)
if __name__=='__main__':unittest.main()
