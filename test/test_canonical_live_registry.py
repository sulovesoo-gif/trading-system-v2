import unittest
from src.live_registry import LiveStrategyRegistryError, LiveStrategyRegistryRepository

class CanonicalRegistryTest(unittest.TestCase):
 def test_requires_exactly_four_frozen_strategy_ids(self):
  rows=[(3,294,'HYNIX_S3_SHORT','A','N','000660','SHORT','0197X0','LONG',1000000,'Y'),(4,299,'HYNIX_S3_SHORT','B','N','000660','SHORT','0197X0','LONG',1000000,'Y'),(5,802,'SAMSUNG_S1_LONG','C','N','005930','LONG','0193W0','LONG',1000000,'Y'),(6,623,'SAMSUNG_S2_SHORT','D','N','005930','SHORT','0193L0','LONG',1000000,'Y')]
  class Cursor:
   def __enter__(s): return s
   def __exit__(s,*_): return False
   def execute(s,*_): pass
   def fetchall(s): return rows
  class Conn:
   def __enter__(s): return s
   def __exit__(s,*_): return False
   def cursor(s): return Cursor()
  self.assertEqual([x.strategy_instance_id for x in LiveStrategyRegistryRepository(Conn).resolve_canonical_live()],['LIVE_STRATEGY_3','LIVE_STRATEGY_4','LIVE_STRATEGY_5','LIVE_STRATEGY_6'])
